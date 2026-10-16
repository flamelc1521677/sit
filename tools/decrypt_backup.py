"""Decrypt a private backup to a new ZIP file; never overwrite live project state."""
import argparse
import base64
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"OFFSAAS-BACKUP-1\n"


def decrypt(backup, key_file):
    data = Path(backup).read_bytes()
    if not data.startswith(MAGIC):
        raise ValueError("不支持的备份格式")
    key = base64.urlsafe_b64decode(Path(key_file).read_text(encoding="ascii").strip())
    offset = len(MAGIC)
    return AESGCM(key).decrypt(data[offset:offset + 12], data[offset + 12:], MAGIC)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument("key_file", type=Path)
    parser.add_argument("output_zip", type=Path)
    args = parser.parse_args()
    plaintext = decrypt(args.backup, args.key_file)
    with args.output_zip.open("xb") as handle:
        handle.write(plaintext)
    print("解密完成。ZIP 含密码及运行数据，请保留在可信本机，不要上传解密后的文件。")
