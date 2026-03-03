"""
Created on Wed Feb 25 16:08:13 2026
csv 읽기, out_df, comment_map 생성 코드

@author: fallin_lyw

"""
# preprocessing.py

from __future__ import annotations
import re
import math
from dataclasses import dataclass
from typing import List, Dict, Optional, Set, Tuple
import pandas as pd


class PLCPreprocessor:

    # =========================
    # 내부 데이터 구조
    # =========================

    @dataclass
    class Token:
        step: Optional[str]
        ins: str
        ops: List[str]

    @dataclass
    class RungExpr:
        coil: str
        expr: str
        ins: str

    CMP_SUFFIX_MAP = {
        "=": "==",
        "<>": "!=",
        "<": "<",
        ">": ">",
        "<=": "<=",
        ">=": ">=",
    }

    TWO_OPERAND_INS_PREFIX = ("LD", "AND", "OR")
    RESERVED_WORDS = {"NOT", "AND", "OR", "XOR", "SET_IF", "RST_IF", "LATCH", "CMP", "UNRESOLVED", "FALSE", "TRUE"}
    DEVICE_RE = re.compile(r"\b[A-Z]{1,3}\d+[A-Z0-9]*\b")

    # =========================
    # 공통 유틸
    # =========================

    @staticmethod
    def _is_nan(x) -> bool:
        return x is None or (isinstance(x, float) and math.isnan(x)) or str(x).strip().lower() == "nan"

    @staticmethod
    def _s(x) -> str:
        return str(x).strip()

    @staticmethod
    def _upper(x) -> str:
        return str(x).strip().upper()

    @staticmethod
    def _not(term: str) -> str:
        return f"NOT({term})"

    @staticmethod
    def _bin(a: str, op: str, b: str) -> str:
        return f"({a}) {op} ({b})"

    @staticmethod
    def _or_join(exprs: List[str]) -> str:
        if not exprs:
            return ""
        if len(exprs) == 1:
            return exprs[0]
        return " OR ".join(f"({e})" for e in exprs)

    # =========================
    # IL CSV 로드
    # =========================

    def load_gxworks2_il_csv(self, path: str) -> pd.DataFrame:
        header_idx = None
        with open(path, "r", encoding="utf-16") as f:
            lines = list(f)

        for i, line in enumerate(lines):
            first = line.split("\t")[0].strip().strip('"').upper()
            if first == "STEP NO.":
                header_idx = i
                break

        if header_idx is None:
            raise ValueError("IL CSV에서 'Step No.' 헤더 행을 찾지 못했습니다.")

        header_cols = [c.strip().strip('"') for c in lines[header_idx].split("\t")]
        header_cols_u = [c.upper().replace(" ", "") for c in header_cols]

        def find_col(name_key: str):
            for idx, cu in enumerate(header_cols_u):
                if cu == name_key:
                    return idx
            return None

        step_col = find_col("STEPNO.")
        ins_col = find_col("INSTRUCTION")

        dev_col = None
        for idx, cu in enumerate(header_cols_u):
            if "I/O" in cu and "DEVICE" in cu:
                dev_col = idx
                break

        if step_col is None or ins_col is None or dev_col is None:
            raise ValueError("필수 컬럼 인덱스를 찾지 못했습니다.")

        rows = []
        numeric_step = re.compile(r"^\d+$")

        for line in lines[header_idx + 1:]:
            cols = [c.strip().strip('"') for c in line.split("\t")]
            if len(cols) <= max(step_col, ins_col, dev_col):
                cols += [""] * (max(step_col, ins_col, dev_col) + 1 - len(cols))

            step = cols[step_col].strip()
            ins = cols[ins_col].strip().upper()
            dev = cols[dev_col].strip()

            if not numeric_step.match(step):
                continue
            if ins == "" and dev == "":
                continue

            rows.append((step, ins, dev))

        df = pd.DataFrame(rows, columns=["step", "ins", "dev"])
        df["ins"] = df["ins"].astype(str).str.strip().str.upper()
        df["dev"] = df["dev"].astype(str).str.strip()
        return df.reset_index(drop=True)

    # =========================
    # Tokenize
    # =========================

    def tokenize_il(self, df: pd.DataFrame) -> List[Token]:
        tokens: List[self.Token] = []

        for _, row in df.iterrows():
            step = row["step"]
            ins = row["ins"]
            dev = row["dev"]

            if ins != "":
                tokens.append(self.Token(step=step, ins=ins, ops=([dev] if dev else [])))

        return tokens

    # =========================
    # Rung 파싱
    # =========================

    def parse_tokens_to_rungs(self, tokens: List[Token]) -> List[RungExpr]:
        acc: Optional[str] = None
        rungs: List[self.RungExpr] = []

        for t in tokens:
            ins = t.ins
            ops = t.ops

            if ins == "LD":
                acc = ops[0] if ops else None
            elif ins == "LDI":
                acc = self._not(ops[0]) if ops else None
            elif ins == "AND":
                acc = self._bin(acc, "AND", ops[0])
            elif ins == "OR":
                acc = self._bin(acc, "OR", ops[0])
            elif ins in ("OUT", "SET", "RST"):
                if ops:
                    rungs.append(self.RungExpr(coil=ops[0], expr=acc, ins=ins))
                    acc = None

        return rungs

    # =========================
    # 통합 함수
    # =========================

    def reconstruct_logic_expressions(self, il_csv_path: str) -> pd.DataFrame:
        df = self.load_gxworks2_il_csv(il_csv_path)
        tokens = self.tokenize_il(df)
        rungs = self.parse_tokens_to_rungs(tokens)
        return pd.DataFrame(
            [{"coil": r.coil.upper(), "ins": r.ins, "expr": r.expr} for r in rungs]
        )

    def merge_il_csv_files(self, il_csv_paths: List[str], output_path: str) -> pd.DataFrame:
        if not il_csv_paths:
            raise ValueError("병합할 IL CSV 파일이 없습니다.")

        merged_frames = []
        for path in il_csv_paths:
            df = self.load_gxworks2_il_csv(path)
            if df.empty:
                continue
            merged_frames.append(df)

        if not merged_frames:
            raise ValueError("병합 가능한 IL CSV 데이터가 없습니다.")

        merged_df = pd.concat(merged_frames, ignore_index=True)
        merged_df["step"] = [str(idx) for idx in range(1, len(merged_df) + 1)]

        export_df = merged_df.rename(
            columns={
                "step": "Step No.",
                "ins": "Instruction",
                "dev": "I/O Device",
            }
        )
        export_df.to_csv(output_path, sep="\t", index=False, encoding="utf-16")
        return merged_df

    def build_logic_with_comments(
        self,
        il_csv_path: str,
        comment_csv_path: Optional[str] = None,
    ):
        out_df = self.reconstruct_logic_expressions(il_csv_path)
    
        comment_map: Dict[str, str] = {}
        comment_table_used = pd.DataFrame(columns=["device", "comment"])
        comment_table_all = pd.DataFrame(columns=["device", "comment"])
    
        if comment_csv_path:
            # 1) comment 전체 읽기
            comment_df = pd.read_csv(
                comment_csv_path,
                sep="\t",
                encoding="utf-16",
                skiprows=2,
                names=["device", "comment"],
                header=None
            )
    
            comment_df["device"] = comment_df["device"].apply(
                lambda x: str(x).strip().upper() if not self._is_nan(x) else ""
            )
            comment_df["comment"] = comment_df["comment"].apply(
                lambda x: str(x).strip() if not self._is_nan(x) else ""
            )
            comment_df = comment_df[comment_df["device"] != ""]
    
            # (옵션) 전체 표도 갖고 싶으면
            comment_table_all = comment_df.drop_duplicates(subset=["device"], keep="last").copy()
            comment_table_all = comment_table_all.sort_values("device").reset_index(drop=True)
    
            # 2) out_df에서 실제 사용된 디바이스 추출 (coil + expr 토큰)
            used_devices: Set[str] = set(out_df["coil"].astype(str).str.upper())
            for e in out_df["expr"].fillna("").astype(str):
                for t in self.DEVICE_RE.findall(e.upper()):
                    used_devices.add(t.upper())
    
            # 3) used_devices로 comment 필터링
            comment_table_used = comment_df[comment_df["device"].isin(used_devices)].copy()
            comment_table_used = comment_table_used.drop_duplicates(subset=["device"], keep="last")
            comment_table_used = comment_table_used.sort_values("device").reset_index(drop=True)
    
            # 4) used 기반 comment_map 생성 (UI/매핑용)
            comment_map = dict(zip(comment_table_used["device"], comment_table_used["comment"]))
    
        # ✅ out_df는 주석 없이 유지
        # ✅ comment_table_used는 "이번 IL 로직에서 실제로 사용된 디바이스만" 들어있음
        return out_df, comment_map, comment_table_used

    
