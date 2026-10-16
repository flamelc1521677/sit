# 私密数据加密备份

`private-data-20261016.enc` 包含本机备份时的登录、数据库、审批账号配置，以及 `.state/`、`artifacts/` 和 `assets/identity_front.png`。使用 AES-256-GCM 加密并校验完整性。

解密密钥保存在原电脑项目的 `.secrets/github-backup-20261016.key`，不上传 GitHub。换电脑恢复时需另行携带该密钥；只有仓库文件无法解密。

```powershell
.\.venv\Scripts\python.exe tools/decrypt_backup.py backups/private-data-20261016.enc .secrets/github-backup-20261016.key private-data.decrypted.zip
```

输出 ZIP 中的 `credentials.env` 是密码配置备份，脚本不会自动加载它。可按需导入环境变量。业务状态恢复前先确认后台最新记录和号码占用情况，不能用旧快照覆盖更新的状态；本工具只输出 ZIP，不直接改写运行数据。图片、日志及状态的 SHA-256 清单位于 ZIP 内的 `manifest.json`。
