# -*- coding: utf-8 -*-
"""
Created on Wed Feb 25 17:56:29 2026
주석기반 추적 & 검증 부분 로직
@author: fallin_lyw
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Set, Optional
import pandas as pd
import re


#주석에 CMD 존재 코일 -> I/L 타겟 코일 지정

@dataclass
class CoilTraceResult:
    coil: str
    exists_in_out_df: bool
    rung_count: int
    expr_list: List[str]
    used_devices: List[str]


# 1. comment_tabledㅔ서 "CMD 관련 코일 뽑기"
# 2. out_df에서 그 코일이 실제로 어떤 조건(expr)로 구동되는지 추적
# 3. 그 조건식에 등장하는 디바이스 목록 뽑기
class ILInterlockTracer:
    """
    out_df(코일-논리식) + comment_table(디바이스-주석) 기반으로
    CMD 코일 타겟을 뽑고, out_df에서 존재 여부/조건식/참조 디바이스를 추적하는 클래스
    """

    DEVICE_RE = re.compile(r"\b[A-Z]{1,3}\d+[A-Z0-9]*\b")

    def __init__(self, out_df: pd.DataFrame, comment_table: pd.DataFrame):
        self.out_df = out_df.copy()
        self.comment_table = comment_table.copy()

        # 기본 정리
        self._normalize_inputs()

        # comment_map도 같이 만들어두면 UI/리포트에 편함
        self.comment_map: Dict[str, str] = dict(
            zip(self.comment_table["device"], self.comment_table["comment"])
        )

    # -------------------------
    # Public APIs
    # -------------------------

    def get_target_cmd_coils(self) -> List[str]:
        """
        comment_table의 comment에 'cmd' 포함된 device 리스트 반환
        """
        df = self.comment_table
        mask = df["comment"].str.contains("cmd", case=False, na=False)
        targets = df.loc[mask, "device"].dropna().unique().tolist()
        return [t for t in targets if t]

    def get_target_cmd_coils_in_out_df(self) -> List[str]:
        """
        CMD 코일 중 out_df에 실제 존재하는 코일만 반환
        """
        targets = self.get_target_cmd_coils()
        out_coils = set(self.out_df["coil"].unique().tolist())
        return [c for c in targets if c in out_coils]

    def trace_coil(self, coil: str) -> CoilTraceResult:
        """
        특정 코일(coil)에 대해:
        - out_df 존재 여부
        - 해당 코일 rung 개수
        - expr 리스트
        - expr에 등장하는 디바이스 토큰 리스트
        를 반환
        """
        coil = str(coil).strip().upper()

        rows = self.out_df[self.out_df["coil"] == coil]
        exists = len(rows) > 0
        expr_list = rows["expr"].fillna("").astype(str).tolist()

        used: Set[str] = set()
        for e in expr_list:
            for t in self.DEVICE_RE.findall(e.upper()):
                used.add(t.upper())

        used_devices = sorted(used)
        return CoilTraceResult(
            coil=coil,
            exists_in_out_df=exists,
            rung_count=len(rows),
            expr_list=expr_list,
            used_devices=used_devices
        )

    def trace_all_targets(self, only_in_out_df: bool = True) -> List[CoilTraceResult]:
        """
        타겟 CMD 코일 전체를 trace
        - only_in_out_df=True면 out_df에 있는 것만 추적
        """
        targets = self.get_target_cmd_coils_in_out_df() if only_in_out_df else self.get_target_cmd_coils()
        return [self.trace_coil(c) for c in targets]

    def to_summary_table(self, results: List[CoilTraceResult]) -> pd.DataFrame:
        """
        trace 결과를 DataFrame으로 변환 (스파이더에서 보기 좋게)
        """
        columns = ["coil", "exists_in_out_df", "rung_count", "expr_count", "used_device_count"]
        rows = [
            {
                "coil": r.coil,
                "exists_in_out_df": r.exists_in_out_df,
                "rung_count": r.rung_count,
                "expr_count": len(r.expr_list),
                "used_device_count": len(r.used_devices),
            }
            for r in results
        ]
        if not rows:
            return pd.DataFrame(columns=columns)
        return (
            pd.DataFrame(rows, columns=columns)
            .sort_values(["exists_in_out_df", "rung_count", "coil"], ascending=[False, False, True])
            .reset_index(drop=True)
        )

    # -------------------------
    # Internal
    # -------------------------

    def _normalize_inputs(self):
        # out_df 컬럼 체크
        required_out = {"coil", "ins", "expr"}
        if not required_out.issubset(set(self.out_df.columns)):
            raise ValueError(f"out_df must have columns {required_out}. got={list(self.out_df.columns)}")

        # comment_table 컬럼 체크
        required_cmt = {"device", "comment"}
        if not required_cmt.issubset(set(self.comment_table.columns)):
            raise ValueError(f"comment_table must have columns {required_cmt}. got={list(self.comment_table.columns)}")

        # 정규화
        self.out_df["coil"] = self.out_df["coil"].fillna("").astype(str).str.strip().str.upper()
        self.out_df["ins"] = self.out_df["ins"].fillna("").astype(str).str.strip().str.upper()
        self.out_df["expr"] = self.out_df["expr"].fillna("").astype(str)

        self.comment_table["device"] = self.comment_table["device"].fillna("").astype(str).str.strip().str.upper()
        self.comment_table["comment"] = self.comment_table["comment"].fillna("").astype(str).str.strip()
        self.comment_table = self.comment_table[self.comment_table["device"] != ""].copy()
        self.comment_table = self.comment_table.drop_duplicates(subset=["device"], keep="last").reset_index(drop=True)
        
    
    @staticmethod
    def expr_to_comment_expr(expr: str, comment_map: dict) -> str:
        """
        expr 안의 디바이스 토큰을 comment_map의 주석으로 치환해서 사람이 읽기 쉬운 expr2 생성.
        - 주석이 없으면 원래 디바이스 토큰 유지
        - 표현 형태: 주석만 쓰면 중복/충돌 가능 → 권장: '주석[디바이스]' 형태
        """
        if expr is None or (isinstance(expr, float) and pd.isna(expr)):
            return ""
    
        s = str(expr)
    
        def repl(m):
            tok = m.group(0).upper()
            cmt = comment_map.get(tok)
            if cmt and str(cmt).strip():
                # 주석만 쓰면 같은 이름 충돌 가능해서 '주석[DEV]' 권장
                return f"{cmt}[{tok}]"
            return tok
    
        return ILInterlockTracer.DEVICE_RE.sub(repl, s)
    
    
    
        
        
        
