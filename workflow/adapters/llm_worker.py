"""llm_worker.py — Adapter for invoking role-based cognitive workers.
Loads system prompts and schemas for Probe, Solver, Auditor, Architect, and Student-Task workers.
"""

import json
import os
from typing import Dict, Any, Optional, Callable


class LLMWorkerAdapter:
    def __init__(self, prompts_dir: Optional[str] = None, schemas_dir: Optional[str] = None):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.prompts_dir = prompts_dir or os.path.abspath(os.path.join(current_dir, "..", "..", "prompts"))
        self.schemas_dir = schemas_dir or os.path.abspath(os.path.join(current_dir, "..", "..", "schemas"))
        self.model_callable: Optional[Callable[[str, str], str]] = None

    def set_model_callable(self, fn: Callable[[str, str], str]) -> None:
        """Set a callable (system_prompt, user_content) -> response_text."""
        self.model_callable = fn

    def load_prompt(self, prompt_filename: str) -> str:
        path = os.path.join(self.prompts_dir, prompt_filename)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def load_schema(self, schema_filename: str) -> Dict[str, Any]:
        path = os.path.join(self.schemas_dir, schema_filename)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def run_worker(
        self,
        role: str,
        prompt_file: str,
        user_input: str,
        fallback_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Dispatch a role-based task to LLM or fallback."""
        sys_prompt = self.load_prompt(prompt_file)

        if self.model_callable:
            raw_output = self.model_callable(sys_prompt, user_input)
            try:
                # Strip markdown code blocks if present
                clean = raw_output.strip()
                if clean.startswith("```json"):
                    clean = clean[7:]
                if clean.startswith("```"):
                    clean = clean[3:]
                if clean.endswith("```"):
                    clean = clean[:-3]
                return json.loads(clean.strip())
            except Exception as e:
                return {"_error": f"Failed to parse LLM JSON output: {e}", "raw": raw_output}

        # If no active LLM callable is provided, use fallback data
        if fallback_data is not None:
            return fallback_data

        return {"status": "unconfigured_worker", "role": role}
