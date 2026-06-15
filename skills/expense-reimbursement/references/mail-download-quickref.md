# 从飞书邮箱下载发票附件快捷参考

本 reference 覆盖"搜某日期的发票邮件→下载PDF附件→落盘到票据目录"这条前置链路。
正式流程在 `lark-mail` 技能的 `references/invoice-workflow.md`；这里只记录最短可用的 lark-cli 命令序列。

## 1. 搜索邮件

```bash
lark-cli mail +triage --as user --query "发票" \
  --filter '{"folder":"INBOX","time_range":{"start_time":"2026-06-13T00:00:00+08:00","end_time":"2026-06-13T23:59:59+08:00"}}' \
  --max 50 --format json
```

## 2. 批量读取邮件（获取附件 ID）

```bash
lark-cli mail +messages --as user \
  --message-ids "msg_id_1,msg_id_2,..." --html=false
```

## 3. 获取附件下载链接

```bash
lark-cli mail user_mailbox.message.attachments download_url --as user \
  --params '{"user_mailbox_id":"me","message_id":"...","attachment_ids":["att_id_1","att_id_2"]}' \
  --format json
```

> ⚠️ 子命令是 `user_mailbox.message.attachments download_url`，不是 `+attachment-download`（后者不存在）。

## 4. curl 下载附件到票据目录

```bash
mkdir -p 票据
cd 票据
curl -s -o "文件名.pdf" "<download_url>"
# 批量：逐行 curl，所有文件下完后 ls -la *.pdf 确认
```

> 注意：download_url 有时效，拿到后尽快下载。

## 5. 与 expense-reimbursement 第一步衔接

下载完成后票据目录结构示例：
```
票据/
  滴滴电子发票.pdf
  滴滴出行行程报销单.pdf
  机票电子普通发票.pdf
  机票出行单.pdf
  入住凭证.pdf
  住宿电子发票.pdf
  12306火车票.pdf
```

直接传给 ADP 抽取：
```bash
python .../scripts/adp_extract.py --dir ./票据 --out ./extract.json
```
