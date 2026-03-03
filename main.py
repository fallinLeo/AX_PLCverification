"""
Created on Wed Feb 25 16:08:13 2026
메인 실행 코드

@author: fallin_lyw

"""

import os
import sys
from preprocessing import PLCPreprocessor
from tracing import ILInterlockTracer
from pyqt import run_app

import pandas as pd

# FILE_DIR = r"C:\Users\LGES\Desktop\AX_Proj"
# IL_CSV = "U02_Cell_Grade_LD_TR.csv"
# COMMENT_CSV = "COMMENT.csv"


def build_default_debug_tables():
    il_csv_path = os.path.join(FILE_DIR, IL_CSV)
    comment_csv_path = os.path.join(FILE_DIR, COMMENT_CSV)

    processor = PLCPreprocessor()
    out_df_local, comment_map_local, comment_table_local = processor.build_logic_with_comments(
        il_csv_path=il_csv_path,
        comment_csv_path=comment_csv_path,
    )

    tracer = ILInterlockTracer(out_df=out_df_local, comment_table=comment_table_local)
    cmd_coils = tracer.get_target_cmd_coils_in_out_df()
    cmd_df_local = pd.DataFrame({"cmd_coil": cmd_coils}).sort_values("cmd_coil").reset_index(drop=True)

    cmd_table = comment_table_local[comment_table_local["device"].isin(cmd_coils)].copy()
    cmd_table = cmd_table.rename(columns={"device": "coil"})

    cmd_df_intable_local = pd.merge(
        cmd_table[["coil", "comment"]],
        out_df_local[["coil", "ins", "expr"]],
        on="coil",
        how="inner",
    ).sort_values("coil").reset_index(drop=True)

    comment_map_local = tracer.comment_map
    cmd_df_intable_local["expr2"] = cmd_df_intable_local["expr"].apply(
        lambda x: tracer.expr_to_comment_expr(x, comment_map_local)
    )

    trace_df = tracer.to_summary_table(tracer.trace_all_targets())
    cmd_df_intable_local = cmd_df_intable_local.merge(trace_df, on="coil", how="left")

    return out_df_local, comment_map_local, comment_table_local, cmd_df_local, cmd_df_intable_local


def main():
    global out_df, comment_map, comment_table, cmd_df, cmd_df_intable
    # out_df, comment_map, comment_table, cmd_df, cmd_df_intable = build_default_debug_tables()
    sys.exit(run_app(sys.argv))


if __name__ == "__main__":
    main()
