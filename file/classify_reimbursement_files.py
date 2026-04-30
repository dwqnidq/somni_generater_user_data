#!/usr/bin/env python3
"""调用大模型判断 file 目录下 3 个 PDF 的报销类型。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FILE_DIR = PROJECT_ROOT / "file"


def _extract_pdf_text(pdf_path: Path, max_chars: int = 4000) -> str:
    """优先 pypdf，其次 pdftotext，最后尝试从二进制中提取可见文本。"""
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(pdf_path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
        if text:
            return text[:max_chars]
    except Exception:
        pass

    cmd = ["pdftotext", "-enc", "UTF-8", str(pdf_path), "-"]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
    except FileNotFoundError:
        return ""

    if result.returncode != 0:
        return ""

    text = (result.stdout or "").strip()
    if not text:
        try:
            raw = pdf_path.read_bytes()
            decoded = raw.decode("latin-1", errors="ignore")
            chunks = re.findall(r"\(([^()]*)\)", decoded)
            text = "\n".join(chunks).strip()
        except Exception:
            return ""
        if not text:
            return ""
    return text[:max_chars]


def _build_messages(pdf_files: list[Path]) -> list[dict]:
    file_blocks = []
    for pdf in pdf_files:
        text = _extract_pdf_text(pdf)
        file_blocks.append(
            {
                "file_name": pdf.name,
                "extracted_text": text or "（未提取到文本）",
            }
        )

    user_prompt = (
        "帮我识别一下这三个文件当中哪一个是对公报销哪一个是员工报销。\n"
        "请严格返回 JSON，不要输出其他说明。格式如下：\n"
        "{\n"
        '  "results": [\n'
        '    {"file_name": "xxx.pdf", "category": "对公报销|员工报销|无法判断", "reason": "简短依据"}\n'
        "  ]\n"
        "}\n"
        f"以下是三个文件的信息：\n{json.dumps(file_blocks, ensure_ascii=False, indent=2)}"
    )
    return [{"role": "user", "content": user_prompt}]


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")

    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    base_url = (
        os.getenv("QWEN_BASE_URL")
        or os.getenv("DASHSCOPE_BASE_URL")
        or os.getenv("TRANSLATE_BASE_URL")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).rstrip("/")
    # 强制优先走千问配置，避免回落到豆包 MODEL_NAME。
    model_name = (
        os.getenv("QWEN_MODEL_NAME")
        or os.getenv("TRANSLATE_MODEL_NAME")
        or "qwen-plus"
    )

    if not api_key:
        print("未找到 DASHSCOPE_API_KEY，请先在 .env 或环境变量配置。")
        return 1

    pdf_files = sorted(FILE_DIR.glob("*.pdf"))
    if len(pdf_files) != 3:
        print(f"期望在 {FILE_DIR} 找到 3 个 PDF，当前为 {len(pdf_files)} 个。")
        return 1

    payload = {
        "model": model_name,
        "temperature": 0,
        "messages": _build_messages(pdf_files),
    }

    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        data = resp.json()
    except Exception as exc:
        print(f"请求失败: {exc}")
        return 1

    if resp.status_code >= 400 or "error" in data:
        print(f"模型接口报错: HTTP {resp.status_code}, body={data}")
        return 1

    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        print(f"返回结构异常: {data}")
        return 1

    output_path = FILE_DIR / "reimbursement_classification_result.json"
    try:
        parsed = json.loads(content)
    except Exception:
        parsed = {"raw_response": content}
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)

    print(content)
    print(f"\n已保存到: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
