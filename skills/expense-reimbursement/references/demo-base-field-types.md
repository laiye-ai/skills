# 演示 Base 字段类型对照（"报销流程场景" → "报销单管理" 表，table=tblIuvtmw3d4PAAz）

> 每次写回前必须先 `base +field-list` 确认当前状态，本文是 2026-06-14 的快照，选项可能被手动增删。

## 关键字段类型及写回方式

| 表字段名 | 字段 ID | 类型 | 写回格式 | 注意 |
|---------|---------|------|---------|------|
| 报销人 | fldeH8ha0x | **user** | `[{"id":"ou_xxx"}]` | 不是文本！需用 `contact +search-user` 解析 open_id。搜不到的人（如外部人员）填 null 留空 |
| 一级费用类型 | fldSUlOQx3 | **select** | `"差旅费用"` | 选项：差旅费用/交通服务/餐饮服务/办公服务/通讯服务/娱乐服务/业务招待费。ADP 输出"差旅费"→映射"差旅费用"；"交通费"→映射"交通服务"；"业务招待费"→表里有对应选项，直接写"业务招待费"。build_reimbursement_table.py 脚本输出的 一级费用类型 可能使用 ADP 原文（如"交通费"），写回前必须映射为 base 选项名 |
| 二级费用明细 | fld3FsAlP7 | **select** | `"差旅费用-住宿"` | 选项仅：差旅费用-住宿/差旅费用-铁路/差旅费用-飞机。其他组合（餐饮/市内交通）无选项→留空 |
| 费用承担部门 | fldCsItBWS | **select** | **不写，跳过** | 该字段由后续审批流程环节填写，不要在 batch-create 中传入 |
| 审批状态 | fldIeVwoui | **select** | `"待主管审批"` | 选项：待主管审批/主管已通过/主管已拒绝/待财务打款/已打款 |
| 关联出差申请编号 | fldcs2Wnkv | **link** → tblBRFMGDFJlaijL | `[{"id":"rec_xxx"}]` | 不是文本编号！第三步读出差申请表时同步记下 `编号→record_id` 映射 |
| 报销金额 | fldKMhX8vC | **number** (currency) | `1550.94` | 数字，别带 ¥ 符号 |
| 金额 | fld2B1DwGD | **number** (currency) | `1463.15` | 同上 |
| 税额 | fldxC3jRBR | **number** (currency) | `87.79` | null 当无值 |
| 消费日期 | fldehwNgBj | **datetime** | `"2026-05-25 00:00:00"` | |
| 报销日期 | fldTfTfxy5 | **datetime** | `"2026-06-14 00:00:00"` | 写入当天 |
| 附件 | fld39AEqC0 | **attachment** | 不在 batch-create 里写 | 拿到 record_id 后用 `+record-upload-attachment` 单独上传，`--file` 必须相对路径 |
| 报销单号 | fldYTv91xD | auto_number | 不写 | 系统自动生成 BX26xxxx |
| 创建人/时间 | fldvCjwbVe/fldbXJhKi1 | created_by/created_at | 不写 | 系统字段 |
| 修改人/时间 | fldKMnVoJS/fldnMHYh8A | updated_by/updated_at | 不写 | 系统字段 |
| 主管审批人 | fldST9rnow | user | 不写 | 审批流填写 |
| 财务经办人 | fldjanFn5P | user | 不写 | 审批流填写 |
| 打款日期/状态/备注 | fldops5YLt/fldbiaI98v/fld3DPrNX8 | — | 不写 | 财务打款后填写 |
| 招待人数 | fld83wwVLe | **text** | `"5"` | 来自餐饮水单，即使 ADP 返回 "5" 也传字符串 |
| 招待客户 | fldKSqNR1c | **text** | `"火星"` | 水单无则留空，等用户补充 |
| 出发地 | fldAYokEjk | **text** | `"北京"` | — |
| 目的地 | fldwyuX4RU | **text** | `"长沙"` | — |
| 开始日期 | fldWb5DB9K | **text** | `"2026-05-25"` | 酒店入住/机票出行开始日期 |
| 结束日期 | fldSZCgDMk | **text** | `"2026-05-29"` | 酒店退房/机票回程日期 |
| 报销事由 | fldWvCMfiL | **text** | `"长沙交流会酒店住宿费"` | agent 按上下文润色脚本默认值 |
| 项目明细 | fldfveajx7 | **text** | `"*住宿服务*住宿费"` | ADP 抽取原文 |
| 收款人 | fldhmgZoau | **text** | `"向儒勋"` | — |
| 收款账号 | fldhxAr4jQ | **text** | `"5444848168319119"` | 16位随机数字串 |
| 收款银行 | fldqaqtk7w | **text** | `"招商银行"` | 固定值 |
| 销售方名称 | fldX7OMGnh | **text** | `"长沙苏湘缘酒店管理有限公司"` | — |
| 发票号码 | flde0AWfZD | **text** | `"26432000001201996396"` | — |
| 报销主体 | fldooQ1kj7 | **text** | `"北京来也网络科技有限公司"` | 取发票购买方名称 |

## `+record-batch-create` 返回的 record_id_list

## `+record-batch-create` 返回的 record_id_list

返回的 `record_id_list` 顺序与输入 `rows` 完全一致（第 0 个 record_id 对应第 0 行），可直接按索引映射后传给 `+record-upload-attachment`。

## 出差申请表（tblBRFMGDFJlaijL）

| 字段 | ID | 类型 |
|------|-----|------|
| 出差申请编号 | fldJZ0nBOo | auto_number (CC26xxxx) |
| 标题 | fldNo8Cr4x | text |
| 出发日期 | fldTZOrjaH | datetime |
| 结束日期 | fldeaK5UfX | datetime |
| 出发地 | fldvkwOwRY | text |
| 目的地 | fldHZrNReQ | text |
| 附件 | fldGN8ezMj | attachment |

## `+record-list` 调用注意

当前 lark-cli (1.0.48) 的 `base +record-list` **不支持** `--page-all` 和 `--json`：
- 正确：`--limit 200 --format json`
- 错误：`--page-all --json`
- 返回的 `has_more` 判断是否全量；数据在 `data.data`（二维数组），字段映射在 `data.fields` 和 `data.field_id_list`

## `contact` 用户搜索

- 正确：`lark-cli contact +search-user --query "姓名" --format json`
- 错误：`lark-cli contact +search`（子命令不存在）
- 返回 `data.users[]` 含 `open_id`、`localized_name`、`enterprise_email`、`department`
