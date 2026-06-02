"""
Upload de arquivos Parquet pra Cloudflare R2 (S3-compatible).

Env vars necessárias:
  R2_ACCOUNT_ID        — ID da conta Cloudflare (32 hex chars)
  R2_ACCESS_KEY_ID     — gerado em R2 > Manage API tokens
  R2_SECRET_ACCESS_KEY — idem
  R2_BUCKET            — nome do bucket (ex: "brinsider-lake")

Sem essas vars, opera em "dry run mode": grava em filesystem local
(LOCAL_LAKE_ROOT, default /tmp/brinsider-lake) para inspeção via DuckDB.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger("siafi.r2")

LOCAL_LAKE_ROOT = Path(os.getenv("LOCAL_LAKE_ROOT", "/tmp/brinsider-lake"))


class LakeWriter:
    """Abstrai destino: R2 (se configurado) ou local."""

    def __init__(self) -> None:
        self.account_id = os.getenv("R2_ACCOUNT_ID")
        self.bucket = os.getenv("R2_BUCKET")
        self.access_key = os.getenv("R2_ACCESS_KEY_ID")
        self.secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
        self._s3 = None

        if self._has_r2_credentials():
            self._init_s3_client()
            logger.info("LakeWriter: R2 mode (bucket=%s)", self.bucket)
        else:
            LOCAL_LAKE_ROOT.mkdir(parents=True, exist_ok=True)
            logger.info("LakeWriter: LOCAL mode (root=%s)", LOCAL_LAKE_ROOT)

    def _has_r2_credentials(self) -> bool:
        return all([self.account_id, self.bucket, self.access_key, self.secret_key])

    def _init_s3_client(self) -> None:
        try:
            import boto3  # type: ignore
            from botocore.config import Config  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "boto3 não instalado. Adicione 'boto3' ao requirements.txt"
            ) from e

        endpoint = f"https://{self.account_id}.r2.cloudflarestorage.com"
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=Config(signature_version="s3v4", region_name="auto"),
        )

    def put(self, local_path: Path, key: str, metadata: Optional[dict[str, str]] = None) -> str:
        """Sobe arquivo local pro destino. Retorna URI canônica (r2:// ou file://)."""
        if self._s3 is not None:
            extra = {"Metadata": metadata} if metadata else {}
            self._s3.upload_file(str(local_path), self.bucket, key, ExtraArgs=extra)
            uri = f"r2://{self.bucket}/{key}"
            logger.info("Uploaded → %s", uri)
            return uri
        else:
            dest = LOCAL_LAKE_ROOT / key
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, dest)
            uri = f"file://{dest}"
            logger.info("Copied → %s", uri)
            return uri

    def exists(self, key: str) -> bool:
        if self._s3 is not None:
            try:
                self._s3.head_object(Bucket=self.bucket, Key=key)
                return True
            except Exception:  # noqa: BLE001
                return False
        else:
            return (LOCAL_LAKE_ROOT / key).exists()
