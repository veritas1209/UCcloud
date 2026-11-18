"""Bounded cloud-init user data accepted by Nova's base64 string schema."""
import base64
import gzip
import json

MAX_USER_DATA_LENGTH = 65535


def encode_cloud_config(config: dict) -> str:
    raw = ("#cloud-config\n" + json.dumps(
        config, ensure_ascii=False, separators=(",", ":"),
    )).encode("utf-8")
    encoded = base64.b64encode(gzip.compress(raw, mtime=0)).decode("ascii")
    if len(encoded) > MAX_USER_DATA_LENGTH:
        raise ValueError("초기 설정 데이터가 너무 큽니다. 함께 설치할 프로그램 수를 줄여 다시 시도해 주세요.")
    return encoded
