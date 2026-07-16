import os
import json
import asyncio
from pathlib import Path
from typing import List, Dict, Any
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from app.core.config import settings

from app.schemas.review import Severity, Category, Finding, CodeReviewResponse

load_dotenv()

# RELATIVE PROMPT LOADING
CURRENT_DIR = Path(__file__).resolve().parent
PROMPT_PATH = CURRENT_DIR.parent.parent / "prompts" / "security_review.md"


def load_security_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"CRITICAL ERROR: Security review prompt file not found at: {PROMPT_PATH}\n"
            f"Please ensure 'security_review.md' exists inside 'app/core/prompts/'."
        )
    return PROMPT_PATH.read_text(encoding="utf-8")


# LINE NUMBER PRE-PROCESSOR
def number_code_lines(code: str) -> str:
    if not code:
        return ""
    lines = code.splitlines()
    return "\n".join(f"{idx + 1:3d} | {line}" for idx, line in enumerate(lines))


# LLM CLIENT SETUP
llm = ChatOpenAI(
    model=os.getenv("DEFAULT_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
    temperature=0.0,
    api_key=os.getenv("LITELLM_API_KEY"),
    base_url=os.getenv("LITELLM_BASE_URL"),
    timeout=25.0,
    max_retries=1,
)

structured_evaluator = llm.with_structured_output(CodeReviewResponse)


async def security_review_node(state: Dict[str, Any]) -> Dict[str, Any]:
    user_input = state.get("user_code") or state.get("user_message", "")
    system_instructions = load_security_prompt()
    numbered_code = number_code_lines(user_input)

    # Explicit schema mapping rules prevent Llama from inventing custom JSON keys
    enforcement_rules = (
        "\n\nCRITICAL JSON SCHEMA OUTPUT RULES:\n"
        "You must strictly use the exact JSON field names and types required by the schema:\n"
        "1. 'line': Must be an INTEGER representing the 1-based line number (e.g., 2). Do NOT use 'location' or strings like 'Line 2'.\n"
        "2. 'severity': Must be exactly one of lowercase strings: 'low', 'medium', 'high', or 'critical'.\n"
        "3. 'category': Must strictly be set to 'security'.\n"
        "4. 'message': Concise summary of the flaw, prepended with the taxonomy class (e.g., '[SQL Injection] Direct string formatting...').\n"
        "5. 'rationale': Detailed explanation of the exploit path and conceptual mitigation guidance.\n"
        "6. 'file_path': Leave as empty string \"\" unless a filename is specified.\n"
        '7. CRITICAL FOR SAFE CODE: If NO security vulnerabilities exist, you MUST return: {"summary": "Code is secure.", "findings": []}.'
    )

    messages = [
        SystemMessage(content=system_instructions + enforcement_rules),
        HumanMessage(
            content=f"Please audit the following numbered Python code submission for security vulnerabilities:\n\n{numbered_code}"
        ),
    ]

    try:
        output: CodeReviewResponse = await structured_evaluator.ainvoke(messages)

        # Enforce Category.SECURITY at the Python code level
        for finding in output.findings:
            finding.category = Category.SECURITY

        return {
            "security_summary": output.summary,
            "security_findings": [f.model_dump() for f in output.findings],
            "current_step": "security_review_completed",
        }

    except (asyncio.TimeoutError, Exception) as e:
        error_msg = f"Provider Execution Error ({type(e).__name__}): {str(e)}"
        print(f"\n [NODE WARNING]: {error_msg}")
        return {
            "security_summary": "Review failed due to provider error.",
            "security_findings": [],
            "error": error_msg,
            "current_step": "security_review_failed",
        }


# LIVE EVIDENCE SUITE
if __name__ == "__main__":

    async def run_live_evidence_suite():
        print("🚀 LAUNCHING LIVE LANGGRAPH SECURITY REVIEW SUITE...\n")
        print("=" * 80)

        test_cases = [
            {
                "name": "TEST 1: SQL Injection (Baseline Case)",
                "code": "def login_user(db, username, password):\n    query = f\"SELECT * FROM users WHERE user = '{username}' AND pass = '{password}'\"\n    return db.execute(query)",
            },
            
            {
                "name": "TEST 3: Safe / Secure Code",
                "code": 'def calculate_area(length: float, width: float) -> float:\n    if length < 0 or width < 0:\n        raise ValueError("Dimensions cannot be negative.")\n    return length * width',
            },
        ]

        for tc in test_cases:
            print(f"\n {tc['name']}")
            print("-" * 50)
            print(number_code_lines(tc["code"]))
            print("-" * 50)

            try:
                print(" Invoking live API...")
                result = await security_review_node({"user_code": tc["code"]})

                print("\n VALIDATED LLM OUTPUT:")
                print(json.dumps(result, indent=2))
            except Exception as e:
                print(f"\n ERROR: {e}")

            print("=" * 80)

            await asyncio.sleep(4)

    asyncio.run(run_live_evidence_suite())
