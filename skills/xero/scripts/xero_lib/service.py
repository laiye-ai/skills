"""Draft-first orchestration for Xero purchase bills."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from .auth import AuthSession
from .errors import AppError, XERO_NOT_FOUND
from .models import (
    AttachmentRequest,
    BillRequest,
    CommandResult,
    ItemRequest,
    SkippedItem,
    ensure_unique_attachment_filenames,
)


ITEM_NOT_FOUND = "ITEM_NOT_FOUND"
ITEM_NOT_PURCHASABLE = "ITEM_NOT_PURCHASABLE"
ITEM_PURCHASE_DETAILS_INCOMPLETE = "ITEM_PURCHASE_DETAILS_INCOMPLETE"


class XeroBillApi(Protocol):
    """The reviewed Xero client operations used by the transaction boundary."""

    def find_contacts(self, search_term: str) -> object: ...

    def session_context(self) -> AuthSession: ...

    def get_item(self, code: str) -> object: ...

    def create_draft(self, payload: dict[str, object]) -> object: ...

    def upload_attachment(self, invoice_id: str, attachment: AttachmentRequest) -> object: ...

    def approve_invoice(self, invoice_id: str) -> object: ...

    def get_invoice(self, invoice_id: str) -> object: ...


class BillService:
    """Create a bill only through the safe DRAFT -> upload -> approve sequence."""

    def __init__(self, api: XeroBillApi) -> None:
        self._api = api

    def create(
        self,
        request: BillRequest,
        *,
        approve: bool = True,
        require_draft_verification: bool = False,
    ) -> CommandResult:
        ensure_unique_attachment_filenames(request.attachments)
        session = self._api.session_context()
        contact_id = self._resolve_contact_id(request.contact_name)
        lines, skipped_items = self._build_lines(request.items)
        if not lines:
            raise AppError("NO_VALID_ITEMS", "No requested items have complete purchase configuration.")

        draft = self._api.create_draft(self._draft_payload(request, contact_id, lines))
        invoice_id = _required_text(draft, ("InvoiceID", "invoice_id"), "Xero draft response")
        uploaded: list[dict[str, object]] = []
        for attachment in request.attachments:
            try:
                self._api.upload_attachment(invoice_id, attachment)
            except AppError as error:
                uploaded.append(_attachment_result(attachment, "failed"))
                observed = self._try_read_back(invoice_id, request, "DRAFT")
                if observed is None:
                    return CommandResult(
                        success=False,
                        message="Bill remains DRAFT because attachment upload and read-back failed.",
                        invoice_id=invoice_id,
                        final_status="DRAFT",
                        skipped_items=tuple(skipped_items),
                        attachments=tuple(uploaded),
                        warnings=(error.code, "MANUAL_INSPECTION_REQUIRED"),
                        **_result_context(session),
                    )
                return CommandResult(
                    success=False,
                    message="Bill remains DRAFT because an attachment upload failed.",
                    invoice_id=invoice_id,
                    final_status=observed["Status"],
                    skipped_items=tuple(skipped_items),
                    attachments=tuple(uploaded),
                    warnings=(error.code,),
                    **_result_context(session, observed),
                )
            uploaded.append(_attachment_result(attachment, "uploaded"))

        draft_observed: dict[str, object] | None = None
        if require_draft_verification and approve and not skipped_items:
            draft_observed = self._try_read_back(invoice_id, request, "DRAFT")
            if draft_observed is None:
                return CommandResult(
                    success=False,
                    message="Demo smoke could not verify the draft before approval.",
                    invoice_id=invoice_id,
                    final_status="UNKNOWN",
                    skipped_items=tuple(skipped_items),
                    attachments=tuple(uploaded),
                    warnings=("MANUAL_INSPECTION_REQUIRED",),
                    **_result_context(session),
                )
            if not draft_observed["matches"]:
                return CommandResult(
                    success=False,
                    message="Demo smoke did not observe the expected DRAFT; approval was not attempted.",
                    invoice_id=invoice_id,
                    final_status=draft_observed["Status"],
                    skipped_items=tuple(skipped_items),
                    attachments=tuple(uploaded),
                    warnings=("READBACK_MISMATCH",),
                    **_result_context(session, draft_observed),
                )

        should_approve = approve and not skipped_items
        expected_status = "AUTHORISED" if should_approve else "DRAFT"
        if should_approve:
            try:
                self._api.approve_invoice(invoice_id)
            except AppError as error:
                observed = self._try_read_back(invoice_id, request, expected_status)
                if observed is None:
                    return CommandResult(
                        success=False,
                        message="Bill approval and read-back did not receive a confirmed Xero response.",
                        invoice_id=invoice_id,
                        final_status="UNKNOWN",
                        skipped_items=tuple(skipped_items),
                        attachments=tuple(uploaded),
                        warnings=(error.code, "MANUAL_INSPECTION_REQUIRED"),
                        **_result_context(session),
                    )
                return CommandResult(
                    success=False,
                    message="Bill approval did not receive a confirmed Xero response.",
                    invoice_id=invoice_id,
                    final_status=observed["Status"],
                    skipped_items=tuple(skipped_items),
                    attachments=tuple(uploaded),
                    warnings=(error.code,),
                    **_result_context(session, observed),
                )

        observed = (
            draft_observed
            if draft_observed is not None and not should_approve
            else self._try_read_back(invoice_id, request, expected_status)
        )
        if observed is None:
            return CommandResult(
                success=False,
                message="Bill read-back did not receive a confirmed Xero response.",
                invoice_id=invoice_id,
                final_status="DRAFT" if skipped_items else "UNKNOWN",
                skipped_items=tuple(skipped_items),
                attachments=tuple(uploaded),
                warnings=("MANUAL_INSPECTION_REQUIRED",),
                **_result_context(session),
            )
        if not observed["matches"]:
            return CommandResult(
                success=False,
                message="Xero read-back did not verify the bill state.",
                invoice_id=invoice_id,
                final_status=observed["Status"],
                skipped_items=tuple(skipped_items),
                attachments=tuple(uploaded),
                warnings=("READBACK_MISMATCH",),
                **_result_context(session, observed),
            )

        warnings: tuple[str, ...] = ()
        if skipped_items:
            warnings = ("Bill remains DRAFT because one or more SKUs were skipped.",)
        elif not approve:
            warnings = ("DEMO_APPROVAL_NOT_REQUESTED",)
        return CommandResult(
            success=True,
            message=(
                "Bill created and verified as DRAFT."
                if not approve
                else "Bill created."
                if skipped_items
                else "Bill created and authorised."
            ),
            invoice_id=invoice_id,
            final_status=observed["Status"],
            skipped_items=tuple(skipped_items),
            attachments=tuple(uploaded),
            warnings=warnings,
            **_result_context(session, observed),
        )

    def _resolve_contact_id(self, requested_name: str) -> str:
        requested = requested_name.strip().casefold()
        contacts = _as_collection(self._api.find_contacts(requested_name), "Contacts")
        matches = [
            contact
            for contact in contacts
            if _is_active_contact(contact)
            and _optional_text(contact, ("Name", "name")) is not None
            and _optional_text(contact, ("Name", "name")).strip().casefold() == requested
        ]
        if not matches:
            raise AppError("CONTACT_NOT_FOUND", "No active Contact exactly matches the requested name.")
        if len(matches) > 1:
            raise AppError("CONTACT_AMBIGUOUS", "More than one active Contact exactly matches the requested name.")
        return _required_text(matches[0], ("ContactID", "contact_id"), "Xero Contact")

    def _build_lines(self, requests: tuple[ItemRequest, ...]) -> tuple[list[dict[str, object]], list[SkippedItem]]:
        lines: list[dict[str, object]] = []
        skipped: list[SkippedItem] = []
        for requested in requests:
            try:
                item = self._api.get_item(requested.code)
            except AppError as error:
                if error.code != XERO_NOT_FOUND:
                    raise
                item = None
            line, reason = _line_from_item(item, requested)
            if line is None:
                skipped.append(SkippedItem(requested.code, reason))
            else:
                lines.append(line)
        return lines, skipped

    @staticmethod
    def _draft_payload(request: BillRequest, contact_id: str, lines: list[dict[str, object]]) -> dict[str, object]:
        return {
            "Type": "ACCPAY",
            "Contact": {"ContactID": contact_id},
            "Date": request.date.isoformat(),
            "DueDate": request.due_date.isoformat(),
            "InvoiceNumber": request.reference,
            "Reference": request.permit_number,
            "LineAmountTypes": "Exclusive",
            "Status": "DRAFT",
            "LineItems": lines,
        }

    def _read_back(self, invoice_id: str, request: BillRequest, expected_status: str) -> dict[str, object]:
        invoice = self._api.get_invoice(invoice_id)
        invoice_number = _optional_text(invoice, ("InvoiceNumber", "invoice_number"))
        reference = _optional_text(invoice, ("Reference", "reference"))
        status = _optional_text(invoice, ("Status", "status"))
        return {
            "Status": status,
            "invoice_number": invoice_number,
            "permit_number": reference,
            "matches": (
                invoice_number == request.reference
                and reference == request.permit_number
                and status == expected_status
            ),
        }

    def _try_read_back(
        self, invoice_id: str, request: BillRequest, expected_status: str
    ) -> dict[str, object] | None:
        try:
            return self._read_back(invoice_id, request, expected_status)
        except AppError:
            return None


def _attachment_result(
    attachment: AttachmentRequest, status: str
) -> dict[str, object]:
    return {
        "path": str(attachment.path),
        "filename": attachment.filename,
        "mime_type": attachment.mime_type,
        "size": attachment.size,
        "status": status,
    }


def _result_context(
    session: AuthSession, observed: dict[str, object] | None = None
) -> dict[str, object]:
    context: dict[str, object] = {"tenant_id": session.tenant_id}
    if session.tenant_name is not None:
        context["tenant_name"] = session.tenant_name
    if observed is not None:
        invoice_number = observed.get("invoice_number")
        permit_number = observed.get("permit_number")
        if isinstance(invoice_number, str):
            context["invoice_number"] = invoice_number
        if isinstance(permit_number, str):
            context["permit_number"] = permit_number
    return context


def decimal_to_json_number(value: Decimal) -> int | float:
    """Convert an already-validated decimal to a JSON number without a string payload."""
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _line_from_item(item: object, requested: ItemRequest) -> tuple[dict[str, object] | None, str]:
    if item is None:
        return None, ITEM_NOT_FOUND
    if _value(item, ("IsPurchased", "IsPurchasable", "is_purchased", "is_purchasable")) is not True:
        return None, ITEM_NOT_PURCHASABLE

    details = _value(item, ("PurchaseDetails", "purchase_details"))
    code = _optional_text(item, ("Code", "code"))
    description = _optional_text(item, ("PurchaseDescription", "purchase_description")) or _optional_text(item, ("Name", "name"))
    unit_price = _as_decimal(
        _value(details, ("UnitPrice", "unit_price"))
        if details is not None
        else _value(item, ("PurchaseUnitPrice", "purchase_unit_price"))
    )
    account_code = (
        _optional_text(details, ("AccountCode", "account_code"))
        if details is not None
        else _optional_text(item, ("PurchaseAccountCode", "purchase_account_code"))
    )
    tax_type = (
        _optional_text(details, ("TaxType", "tax_type"))
        if details is not None
        else _optional_text(item, ("PurchaseTaxType", "purchase_tax_type"))
    )
    if not code or not description or unit_price is None or not account_code:
        return None, ITEM_PURCHASE_DETAILS_INCOMPLETE

    line: dict[str, object] = {
        "ItemCode": code,
        "Description": description,
        "Quantity": decimal_to_json_number(requested.qty),
        "UnitAmount": decimal_to_json_number(unit_price),
        "AccountCode": account_code,
    }
    if tax_type:
        line["TaxType"] = tax_type
    return line, ""


def _as_collection(value: object, collection_key: str) -> list[object]:
    if isinstance(value, (list, tuple)):
        return list(value)
    nested = _value(value, (collection_key, collection_key.lower()))
    if isinstance(nested, (list, tuple)):
        return list(nested)
    return [value] if value is not None else []


def _is_active_contact(contact: object) -> bool:
    status = _value(contact, ("ContactStatus", "contact_status", "Status", "status"))
    if isinstance(status, str):
        return status.strip().upper() == "ACTIVE"
    return _value(contact, ("IsActive", "is_active")) is True


def _value(source: object, names: tuple[str, ...]) -> object:
    if isinstance(source, dict):
        for name in names:
            if name in source:
                return source[name]
        return None
    for name in names:
        value = getattr(source, name, None)
        if value is not None:
            return value
    return None


def _optional_text(source: object, names: tuple[str, ...]) -> str | None:
    value = _value(source, names)
    return value if isinstance(value, str) else None


def _required_text(source: object, names: tuple[str, ...], source_name: str) -> str:
    value = _optional_text(source, names)
    if value and value.strip():
        return value
    raise AppError("XERO_RESPONSE_INVALID", f"{source_name} did not include a required identifier.")


def _as_decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None
