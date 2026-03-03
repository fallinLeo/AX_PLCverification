"""
Created on Wed Feb 26 16:21:13 2026
PYQT-based UI module.

@author: fallin_lyw
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QMessageBox,
    QApplication,
    QFileDialog,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFrame,
    QStackedWidget,
    QTabBar,
)
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QBrush, QColor, QDragEnterEvent, QDropEvent, QFont, QIcon, QPainter, QPen, QPolygon

from preprocessing import PLCPreprocessor
from tracing import ILInterlockTracer
from plc_view.ui_models import Coil, ComponentView, DiagramView, Leaf, ParallelGroup, RungView, SeriesGroup
from plc_view.visualizer import TkLadderRenderer
try:
    from DB_proj.comment_classification import CommentClassifier
except Exception:
    CommentClassifier = None


@dataclass(frozen=True)
class ContactNode:
    name: str


@dataclass(frozen=True)
class NotNode:
    operand: "ExprNode"


@dataclass(frozen=True)
class AndNode:
    operands: list["ExprNode"]


@dataclass(frozen=True)
class OrNode:
    operands: list["ExprNode"]


ExprNode = ContactNode | NotNode | AndNode | OrNode


class _LogicParser:
    TOKEN_RE = re.compile(r"\(|\)|!|\bAND\b|\bOR\b|\bNOT\b|[^\s()]+", re.IGNORECASE)

    def __init__(self, expr: str):
        self.tokens = self.TOKEN_RE.findall(expr.replace(",", " "))
        self.pos = 0

    def parse(self) -> ExprNode:
        if not self.tokens:
            return ContactNode(name="UNKNOWN")
        node = self._parse_or()
        return node

    def _peek(self) -> str | None:
        if self.pos >= len(self.tokens):
            return None
        return self.tokens[self.pos]

    def _take(self) -> str | None:
        token = self._peek()
        if token is not None:
            self.pos += 1
        return token

    def _parse_or(self) -> ExprNode:
        nodes = [self._parse_and()]
        while True:
            token = self._peek()
            if token is None or token.upper() != "OR":
                break
            self._take()
            nodes.append(self._parse_and())
        if len(nodes) == 1:
            return nodes[0]
        flattened: list[ExprNode] = []
        for node in nodes:
            if isinstance(node, OrNode):
                flattened.extend(node.operands)
            else:
                flattened.append(node)
        return OrNode(operands=flattened)

    def _parse_and(self) -> ExprNode:
        nodes = [self._parse_unary()]
        while True:
            token = self._peek()
            if token is None or token.upper() != "AND":
                break
            self._take()
            nodes.append(self._parse_unary())
        if len(nodes) == 1:
            return nodes[0]
        flattened: list[ExprNode] = []
        for node in nodes:
            if isinstance(node, AndNode):
                flattened.extend(node.operands)
            else:
                flattened.append(node)
        return AndNode(operands=flattened)

    def _parse_unary(self) -> ExprNode:
        token = self._peek()
        if token is None:
            return ContactNode(name="UNKNOWN")
        if token == "!" or token.upper() == "NOT":
            self._take()
            return NotNode(operand=self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> ExprNode:
        token = self._take()
        if token is None:
            return ContactNode(name="UNKNOWN")
        if token == "(":
            node = self._parse_or()
            if self._peek() == ")":
                self._take()
            return node
        if token == ")":
            return ContactNode(name="UNKNOWN")
        return ContactNode(name=token.strip())


class DropLineEdit(QLineEdit):
    def __init__(self, placeholder: str, allow_multiple: bool = True, show_full_paths: bool = False):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setAcceptDrops(True)
        self.setReadOnly(True)
        self.allow_multiple = allow_multiple
        self.show_full_paths = show_full_paths
        self._paths: list[str] = []
        self._last_dragged_path: str = ""

    def selected_paths(self) -> list[str]:
        return list(self._paths)

    def set_paths(self, paths: list[str]):
        normalized = []
        seen = set()
        for p in paths:
            if not p:
                continue
            n = os.path.normpath(p)
            if os.path.exists(n) and n.lower().endswith(".csv") and n.lower() not in seen:
                seen.add(n.lower())
                normalized.append(n)
        if not self.allow_multiple and normalized:
            normalized = [normalized[-1]]
        self._paths = normalized
        if self.show_full_paths:
            display_text = "; ".join(self._paths)
        else:
            display_text = "; ".join(os.path.basename(path) for path in self._paths)
        self.setText(display_text)
        self.setToolTip("\n".join(self._paths))
        if self._last_dragged_path and self._last_dragged_path not in self._paths:
            self._last_dragged_path = ""

    def add_paths(self, paths: list[str]):
        if not self.allow_multiple:
            self.set_paths(paths)
            return
        self.set_paths(self.selected_paths() + paths)

    def clear_paths(self):
        self._paths = []
        self._last_dragged_path = ""
        self.setToolTip("")
        self.clear()

    def latest_path_for_analysis(self) -> str:
        if self._last_dragged_path and self._last_dragged_path in self._paths:
            return self._last_dragged_path
        paths = self.selected_paths()
        return paths[-1] if paths else ""

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            csv_urls = [u for u in event.mimeData().urls() if u.toLocalFile().lower().endswith(".csv")]
            if csv_urls:
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        dropped = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile().lower().endswith(".csv")]
        if not dropped:
            return
        if not self.allow_multiple:
            self.set_paths(dropped)
            self._last_dragged_path = os.path.normpath(dropped[-1])
            return
        # Default: replace existing selection. Ctrl + drop: append.
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.add_paths(dropped)
        else:
            self.set_paths(dropped)
        self._last_dragged_path = os.path.normpath(dropped[-1])


class DropTableWidget(QTableWidget):
    def __init__(self, placeholder: str):
        super().__init__()
        self._placeholder = placeholder
        self.setColumnCount(1)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(False)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setShowGrid(True)
        self.setGridStyle(Qt.PenStyle.SolidLine)
        self.setStyleSheet("""
            QTableWidget {
                background: white;
                border: none;
                gridline-color: #e2e2e2;
                selection-background-color: #dbe9ff;
                selection-color: black;
                outline: 0;
            }
            QTableWidget::item {
                padding-left: 6px;
                padding-right: 6px;
            }
        """)
        self.setVerticalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setCornerButtonEnabled(False)
        self._paths: list[str] = []
        self._last_dragged_path: str = ""
        self._remove_button: QPushButton | None = None
        self.itemSelectionChanged.connect(self.update_remove_button_position)
        self.verticalScrollBar().valueChanged.connect(self.update_remove_button_position)
        self._render()

    def selected_paths(self) -> list[str]:
        return list(self._paths)

    def add_paths(self, paths: list[str], append: bool = True):
        cleaned = []
        for p in paths:
            if not p:
                continue
            n = os.path.normpath(p)
            if os.path.exists(n) and n.lower().endswith(".csv"):
                cleaned.append(n)

        if not cleaned:
            return

        if not append:
            self._paths = []

        for p in cleaned:
            if p not in self._paths:
                self._paths.append(p)

        self._render()
        if self._last_dragged_path and self._last_dragged_path not in self._paths:
            self._last_dragged_path = ""

    def _render(self):
        self.clearContents()
        visible_rows = max(len(self._paths), 8)
        self.setRowCount(visible_rows)
        for row, path in enumerate(self._paths):
            item = QTableWidgetItem(os.path.basename(path))
            item.setToolTip(path)
            self.setItem(row, 0, item)
        for row in range(len(self._paths), visible_rows):
            empty_item = QTableWidgetItem("")
            empty_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.setItem(row, 0, empty_item)
        self.update_remove_button_position()

    def clear_paths(self):
        self._paths = []
        self._last_dragged_path = ""
        self._render()

    def set_remove_button(self, button: QPushButton):
        self._remove_button = button
        self.update_remove_button_position()

    def update_remove_button_position(self):
        if self._remove_button is None:
            return

        rows = sorted({index.row() for index in self.selectedIndexes() if index.row() < len(self._paths)})
        if not rows:
            self._remove_button.hide()
            return

        rect = self.visualRect(self.model().index(rows[0], 0))
        if not rect.isValid() or rect.isEmpty():
            self._remove_button.hide()
            return

        x = max(4, self.viewport().width() - self._remove_button.width() - 4)
        y = rect.top() + max(0, (rect.height() - self._remove_button.height()) // 2)
        self._remove_button.move(x, y)
        self._remove_button.raise_()
        self._remove_button.show()

    def remove_selected_paths(self):
        rows = sorted({index.row() for index in self.selectedIndexes()}, reverse=True)
        if not rows or not self._paths:
            return
        removed_paths = {self._paths[row] for row in rows if 0 <= row < len(self._paths)}
        self._paths = [path for idx, path in enumerate(self._paths) if idx not in rows]
        if self._last_dragged_path in removed_paths:
            self._last_dragged_path = self._paths[-1] if self._paths else ""
        self._render()

    def latest_path_for_analysis(self) -> str:
        if self._last_dragged_path and self._last_dragged_path in self._paths:
            return self._last_dragged_path
        paths = self.selected_paths()
        return paths[-1] if paths else ""

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return

        paths = []
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p:
                paths.append(p)

        self.add_paths(paths, append=True)
        if paths:
            self._last_dragged_path = os.path.normpath(paths[-1])
        event.acceptProposedAction()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete:
            self.remove_selected_paths()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_remove_button_position()

    def scrollContentsBy(self, dx: int, dy: int):
        super().scrollContentsBy(dx, dy)
        self.update_remove_button_position()


class SinglePathTableWidget(QTableWidget):
    def __init__(self, placeholder: str):
        super().__init__()
        self._placeholder = placeholder
        self._path = ""
        self._remove_button: QPushButton | None = None
        self.setColumnCount(1)
        self.setRowCount(1)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setShowGrid(True)
        self.setGridStyle(Qt.PenStyle.SolidLine)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("""
            QTableWidget {
                background: white;
                border: 1px solid #cfcfcf;
                gridline-color: #e2e2e2;
                outline: 0;
            }
            QTableWidget::item {
                padding-left: 6px;
                padding-right: 6px;
            }
        """)
        self.itemSelectionChanged.connect(self.update_remove_button_position)
        self._render()

    def selected_paths(self) -> list[str]:
        return [self._path] if self._path else []

    def set_paths(self, paths: list[str]):
        cleaned = []
        for p in paths:
            if not p:
                continue
            n = os.path.normpath(p)
            if os.path.exists(n) and n.lower().endswith(".csv"):
                cleaned.append(n)
        self._path = cleaned[-1] if cleaned else ""
        self._render()

    def clear_paths(self):
        self._path = ""
        self._render()

    def latest_path_for_analysis(self) -> str:
        return self._path

    def set_remove_button(self, button: QPushButton):
        self._remove_button = button
        self.update_remove_button_position()

    def remove_selected_path(self):
        if not self._path:
            return
        rows = {index.row() for index in self.selectedIndexes()}
        if 0 in rows:
            self.clear_paths()

    def update_remove_button_position(self):
        if self._remove_button is None:
            return
        rows = {index.row() for index in self.selectedIndexes()}
        should_show = bool(self._path) and (0 in rows)
        if not should_show:
            self._remove_button.hide()
            return

        rect = self.visualRect(self.model().index(0, 0))
        if not rect.isValid() or rect.isEmpty():
            self._remove_button.hide()
            return

        x = max(4, self.viewport().width() - self._remove_button.width() - 4)
        y = rect.top() + max(0, (rect.height() - self._remove_button.height()) // 2)
        self._remove_button.move(x, y)
        self._remove_button.raise_()
        self._remove_button.show()

    def _render(self):
        self.clearContents()
        if not self._path:
            item = QTableWidgetItem(self._placeholder)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.setItem(0, 0, item)
            self.setToolTip("")
            self.clearSelection()
            self.update_remove_button_position()
            return
        item = QTableWidgetItem(os.path.basename(self._path))
        item.setToolTip(self._path)
        self.setItem(0, 0, item)
        self.setToolTip(self._path)
        self.update_remove_button_position()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete:
            self.remove_selected_path()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_remove_button_position()


class EmptyDbWindow(QWidget):
    def __init__(self, title: str):
        super().__init__()
        self.setWindowTitle(title)
        self.resize(720, 480)

        layout = QVBoxLayout(self)
        label = QLabel("DataFrame 표시 영역 (추후 연결)")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)


class LadderPreviewWindow(QWidget):
    def __init__(self, title: str = "래더 다이어그램 미리보기"):
        super().__init__()
        self.setWindowTitle(title)
        self.resize(940, 560)

        layout = QVBoxLayout(self)
        label = QLabel("래더 다이어그램 표시 영역 (추후 연결)")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)


class DownArrowWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(60, 48)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#B7E36D")))
        painter.drawRect(21, 2, 18, 18)
        painter.drawPolygon(
            QPolygon(
                [
                    QPoint(8, 20),
                    QPoint(52, 20),
                    QPoint(30, 42),
                ]
            )
        )


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PLC CSV Analyzer")
        self.resize(1280, 780)
        self.setWindowIcon(QIcon(r"C:\Users\LGES\Desktop\AX_Proj\Labubu.png"))

        self.out_df = pd.DataFrame()
        self.comment_map = {}
        self.comment_table = pd.DataFrame()
        self.cmd_df = pd.DataFrame()
        self.cmd_df_intable = pd.DataFrame()
        self.target_db_window: EmptyDbWindow | None = None
        self.interlock_db_window: EmptyDbWindow | None = None
        self.ladder_renderers: list[TkLadderRenderer] = []
        self.comment_classifier_panel: QWidget | None = None

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 14, 20, 14)
        main_layout.setSpacing(0)

        self.page_stack = QStackedWidget()
        main_layout.addWidget(self.page_stack, 1)

        page_1 = QWidget()
        root = QHBoxLayout(page_1)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(22)
        self.page_stack.addWidget(page_1)

        page_2 = QWidget()
        page_2_layout = QVBoxLayout(page_2)
        page_2_layout.setContentsMargins(0, 0, 0, 0)
        if CommentClassifier is not None:
            self.comment_classifier_panel = CommentClassifier(parent=page_2, embedded=True)
            page_2_layout.addWidget(self.comment_classifier_panel)
        else:
            page_2_layout.addStretch(1)
            unavailable_label = QLabel("탭_2 로드 실패: DB_proj/comment_classification.py import 불가")
            unavailable_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            page_2_layout.addWidget(unavailable_label)
            page_2_layout.addStretch(1)
        self.page_stack.addWidget(page_2)

        for idx in range(3, 5):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.addStretch(1)
            empty_label = QLabel(f"탭_{idx} 빈 창")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            page_layout.addWidget(empty_label)
            page_layout.addStretch(1)
            self.page_stack.addWidget(page)

        main_layout.addSpacing(8)

        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(10, 0, 0, 2)
        tab_row.setSpacing(0)
        self.tab_bar = QTabBar()
        self.tab_bar.setDrawBase(False)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setDocumentMode(True)
        self.tab_bar.addTab("탭_1")
        self.tab_bar.addTab("탭_2")
        self.tab_bar.addTab("탭_3")
        self.tab_bar.addTab("탭_4")
        self.tab_bar.currentChanged.connect(self.switch_tab)
        tab_row.addWidget(self.tab_bar)
        tab_row.addStretch(1)
        main_layout.addLayout(tab_row)
        self.switch_tab(0)

        left_col = QVBoxLayout()
        left_col.setSpacing(12)

        il_label = QLabel("IL CSV list")
        left_col.addWidget(il_label, alignment=Qt.AlignmentFlag.AlignLeft)

        self.il_path = DropTableWidget("IL CSV 파일 드래그 앤 드롭 (Ctrl+드롭: 추가)")
        self.il_path.setFixedSize(410, 170)
        left_col.addWidget(self.il_path)

        self.il_remove_btn = QPushButton("제거", self.il_path.viewport())
        self.il_remove_btn.setFixedSize(48, 22)
        self.il_remove_btn.hide()
        self.il_remove_btn.clicked.connect(self.remove_selected_il_csv)
        self.il_path.set_remove_button(self.il_remove_btn)

        comment_label = QLabel("COMMENT CSV")
        left_col.addWidget(comment_label, alignment=Qt.AlignmentFlag.AlignLeft)
        comment_row = QHBoxLayout()
        comment_row.setContentsMargins(0, 0, 0, 0)
        comment_row.setSpacing(8)
        self.comment_path = DropLineEdit("COMMENT CSV 파일 드래그 앤 드롭", allow_multiple=False)
        self.comment_path.setFixedSize(346, 32)
        comment_row.addWidget(self.comment_path)

        self.comment_remove_btn = QPushButton("제거")
        self.comment_remove_btn.setFixedSize(56, 24)
        self.comment_remove_btn.clicked.connect(self.remove_selected_comment_csv)
        comment_row.addWidget(self.comment_remove_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        left_col.addLayout(comment_row)

        load_btn_row = QHBoxLayout()
        load_btn_row.setSpacing(12)
        load_btn_row.setContentsMargins(110, 4, 0, 0)

        self.il_browse_btn = QPushButton("IL CSV 불러오기")
        self.il_browse_btn.setFixedSize(138, 44)
        self.il_browse_btn.clicked.connect(self.pick_il_csv)
        load_btn_row.addWidget(self.il_browse_btn)

        self.comment_browse_btn = QPushButton("COMMENT 불러오기")
        self.comment_browse_btn.setFixedSize(148, 44)
        self.comment_browse_btn.clicked.connect(self.pick_comment_csv)
        load_btn_row.addWidget(self.comment_browse_btn)

        load_btn_row.addStretch(1)
        left_col.addLayout(load_btn_row)

        left_col.addStretch(1)

        self.target_coil_db_btn = QPushButton("타겟 코일 DB")
        self.target_coil_db_btn.setFixedSize(170, 44)
        self.target_coil_db_btn.clicked.connect(self.open_target_coil_db_window)
        left_col.addWidget(self.target_coil_db_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        self.interlock_db_btn = QPushButton("인터락 조건 DB")
        self.interlock_db_btn.setFixedSize(170, 44)
        self.interlock_db_btn.clicked.connect(self.open_interlock_db_window)
        left_col.addWidget(self.interlock_db_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        root.addLayout(left_col, 0)

        center_col = QVBoxLayout()
        center_col.setSpacing(12)
        center_col.setContentsMargins(0, 10, 0, 0)

        center_col.addSpacing(14)
        
        self.merge_btn = QPushButton("CSV 병합")
        self.merge_btn.setFixedSize(170, 62)
        self.merge_btn.clicked.connect(self.merge_csv_files)
        center_col.addWidget(self.merge_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        self.merge_arrow = DownArrowWidget()
        center_col.addWidget(self.merge_arrow, alignment=Qt.AlignmentFlag.AlignHCenter)

        merged_path_row = QHBoxLayout()
        merged_path_row.setContentsMargins(0, 0, 0, 0)
        merged_path_row.setSpacing(6)

        self.merged_csv_path = SinglePathTableWidget("CSV 병합(없을 시 건너뜀)")
        self.merged_csv_path.setFixedSize(250, 30)
        merged_path_row.addWidget(self.merged_csv_path)

        self.merged_remove_btn = QPushButton("제거", self.merged_csv_path.viewport())
        self.merged_remove_btn.setFixedSize(48, 22)
        self.merged_remove_btn.hide()
        self.merged_remove_btn.clicked.connect(self.clear_merged_csv_path)
        self.merged_csv_path.set_remove_button(self.merged_remove_btn)
        center_col.addLayout(merged_path_row)

        self.merge_arrow_2 = DownArrowWidget()
        center_col.addWidget(self.merge_arrow_2, alignment=Qt.AlignmentFlag.AlignHCenter)

        center_col.addSpacing(10) #LYW

        self.run_btn = QPushButton("분석 시작 버튼")
        self.run_btn.setFixedSize(170, 62)
        self.run_btn.setFont(QFont("", weight=QFont.Weight.Bold))
        self.run_btn.clicked.connect(self.run_analysis)
        center_col.addWidget(self.run_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        center_col.addStretch(1)
        root.addLayout(center_col, 0)

        right_col = QVBoxLayout()
        right_col.setSpacing(8)
        self.result_label = QLabel("결과 코일 리스트 출력 창")
        right_col.addWidget(self.result_label, alignment=Qt.AlignmentFlag.AlignLeft)

        self.result_table = QTableWidget()
        self.result_table.setColumnCount(0)
        self.result_table.setRowCount(0)
        self.result_table.setMinimumSize(470, 460)
        self.result_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.result_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.result_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.result_table.verticalHeader().setVisible(True)
        header = self.result_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionsMovable(True)
        header.setStretchLastSection(False)
        self.result_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.result_table.verticalHeader().sectionDoubleClicked.connect(self.on_result_row_header_double_click)
        self.result_table.cellDoubleClicked.connect(self.on_result_cell_double_click)
        right_col.addWidget(self.result_table)

        root.addLayout(right_col, 2)

    def _pick_csv_files(self) -> list[str]:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "CSV 파일 선택",
            "",
            "CSV Files (*.csv);;All Files (*)",
        )
        return paths

    def switch_tab(self, index: int):
        self.page_stack.setCurrentIndex(index)
        self.tab_bar.setCurrentIndex(index)

    def pick_il_csv(self):
        paths = self._pick_csv_files()
        if paths:
            self.il_path.add_paths(paths)

    def pick_comment_csv(self):
        paths = self._pick_csv_files()
        if paths:
            self.comment_path.set_paths(paths[:1])

    def open_target_coil_db_window(self):
        self.target_db_window = EmptyDbWindow("타겟 코일 DB")
        self.target_db_window.show()
        self.target_db_window.raise_()
        self.target_db_window.activateWindow()

    def open_interlock_db_window(self):
        self.interlock_db_window = EmptyDbWindow("인터락 조건 DB")
        self.interlock_db_window.show()
        self.interlock_db_window.raise_()
        self.interlock_db_window.activateWindow()

    def remove_selected_il_csv(self):
        self.il_path.remove_selected_paths()

    def remove_selected_comment_csv(self):
        self.comment_path.clear_paths()

    def clear_merged_csv_path(self):
        self.merged_csv_path.clear_paths()

    def on_result_row_header_double_click(self, row: int):
        if row < 0 or row >= self.result_table.rowCount():
            return
        self.result_table.selectRow(row)
        self._open_ladder_preview_for_row(row)

    def on_result_cell_double_click(self, row: int, column: int):
        del column
        if row < 0 or row >= self.result_table.rowCount():
            return

        selected_rows = {index.row() for index in self.result_table.selectedIndexes()}
        if row not in selected_rows:
            return

        self._open_ladder_preview_for_row(row)

    def _open_ladder_preview_for_row(self, row: int):
        title = f"({row + 1}번 행) 래더 다이어그램"
        coil_col = self._find_result_table_column("coil")
        coil_name = ""
        if coil_col >= 0:
            item = self.result_table.item(row, coil_col)
            if item is not None and item.text():
                coil_name = item.text().strip()
                title = f"{coil_name}({row + 1}번 행) 래더 다이어그램"

        expr_col = self._find_result_table_column("expr")
        if expr_col < 0:
            QMessageBox.warning(self, "표시 불가", "결과 테이블에서 expr 컬럼을 찾지 못했습니다.")
            return

        expr_item = self.result_table.item(row, expr_col)
        expr_text = expr_item.text().strip() if expr_item is not None and expr_item.text() else ""
        if not expr_text:
            QMessageBox.warning(self, "표시 불가", "선택한 행의 expr 값이 비어 있습니다.")
            return

        expr2_col = self._find_result_table_column("expr2")
        expr2_text = ""
        if expr2_col >= 0:
            expr2_item = self.result_table.item(row, expr2_col)
            expr2_text = expr2_item.text().strip() if expr2_item is not None and expr2_item.text() else ""

        comment_col = self._find_result_table_column("comment")
        comment_text = ""
        if comment_col >= 0:
            comment_item = self.result_table.item(row, comment_col)
            comment_text = comment_item.text().strip() if comment_item is not None and comment_item.text() else ""

        output_name = coil_name if coil_name else f"ROW_{row + 1}"
        diagram = self._build_diagram_from_expr(expr_text, output_name, rung_idx=0)

        # lyw Comment기반 rung 추가 위치
        if expr2_text:
            comment_coil_name = self._format_comment_label(output_name, comment_text)
            comment_diagram = self._build_diagram_from_expr(
                expr_text,
                comment_coil_name,
                rung_idx=1,
                label_resolver=self._resolve_comment_label,
            )
            diagram = DiagramView(rungs=[*diagram.rungs, *comment_diagram.rungs])

        renderer = TkLadderRenderer(title=title, width=980, height=620)
        renderer.render(diagram)
        self.ladder_renderers.append(renderer)

    def _find_result_table_column(self, name: str) -> int:
        for col in range(self.result_table.columnCount()):
            header_item = self.result_table.horizontalHeaderItem(col)
            if header_item is not None and header_item.text() == name:
                return col
        return -1

    def _build_diagram_from_expr(
        self,
        expr: str,
        coil_name: str,
        rung_idx: int = 0,
        label_resolver: Callable[[str], str] | None = None,
    ) -> DiagramView:
        normalized_expr = self._normalize_expr(expr)
        ast = self._parse_expr_to_ast(normalized_expr)
        logic = self._ast_to_series_group(ast, rung_idx=rung_idx, label_resolver=label_resolver)
        coil_component = ComponentView(
            id=f"r{rung_idx}_coil",
            name=coil_name,
            kind="coil",
            state=False,
        )
        flat_components = self._flatten_series_components(logic)
        flat_components.append(coil_component)
        return DiagramView(
            rungs=[
                RungView(
                    id=f"r{rung_idx}",
                    components=flat_components,
                    logic=logic,
                    coil=Coil(component=coil_component),
                    power_on=False,
                )
            ]
        )

    @staticmethod
    def _parse_expr_to_ast(expr: str) -> ExprNode:
        parser = _LogicParser(expr)
        return parser.parse()

    def _ast_to_series_group(
        self,
        node: ExprNode,
        rung_idx: int,
        label_resolver: Callable[[str], str] | None = None,
    ) -> SeriesGroup:
        self._component_seq = 0
        return self._ast_to_series_group_inner(node, rung_idx=rung_idx, label_resolver=label_resolver)

    def _ast_to_series_group_inner(
        self,
        node: ExprNode,
        rung_idx: int,
        label_resolver: Callable[[str], str] | None = None,
    ) -> SeriesGroup:
        if isinstance(node, ContactNode):
            contact = self._make_component(rung_idx, node.name, inverted=False, label_resolver=label_resolver)
            return SeriesGroup(items=[Leaf(component=contact)])

        if isinstance(node, NotNode):
            if isinstance(node.operand, ContactNode):
                contact = self._make_component(
                    rung_idx,
                    node.operand.name,
                    inverted=True,
                    label_resolver=label_resolver,
                )
                return SeriesGroup(items=[Leaf(component=contact)])
            name = f"NOT({self._ast_to_text(node.operand)})"
            contact = self._make_component(rung_idx, name, inverted=True, label_resolver=label_resolver)
            return SeriesGroup(items=[Leaf(component=contact)])

        if isinstance(node, AndNode):
            items: list[Leaf | ParallelGroup] = []
            for operand in node.operands:
                items.extend(
                    self._ast_to_series_group_inner(
                        operand,
                        rung_idx=rung_idx,
                        label_resolver=label_resolver,
                    ).items
                )
            return SeriesGroup(items=items)

        if isinstance(node, OrNode):
            branches = [
                self._ast_to_series_group_inner(
                    operand,
                    rung_idx=rung_idx,
                    label_resolver=label_resolver,
                )
                for operand in node.operands
            ]
            return SeriesGroup(items=[ParallelGroup(branches=branches)])

        unknown = self._make_component(rung_idx, "UNKNOWN", inverted=False, label_resolver=label_resolver)
        return SeriesGroup(items=[Leaf(component=unknown)])

    def _make_component(
        self,
        rung_idx: int,
        name: str,
        inverted: bool,
        label_resolver: Callable[[str], str] | None = None,
    ) -> ComponentView:
        display_name = name if name else "UNKNOWN"
        if label_resolver is not None:
            display_name = label_resolver(display_name)
        component = ComponentView(
            id=f"r{rung_idx}_c{self._component_seq}",
            name=display_name,
            kind="inverted_contact" if inverted else "contact",
            state=False,
        )
        self._component_seq += 1
        return component

    def _resolve_comment_label(self, token: str) -> str:
        raw = token.strip()
        if not raw:
            return "UNKNOWN"
        upper = raw.upper()
        if re.fullmatch(r"[A-Z]{1,3}\d+[A-Z0-9]*", upper) is None:
            return raw
        comment = str(self.comment_map.get(upper, "")).strip() if isinstance(self.comment_map, dict) else ""
        if comment:
            return comment
        return upper

    @staticmethod
    def _format_comment_label(coil_name: str, coil_comment: str) -> str:
        coil = coil_name.strip()
        comment = coil_comment.strip()
        if comment:
            return comment
        if coil:
            return coil
        return "UNKNOWN"

    @staticmethod
    def _flatten_series_components(series: SeriesGroup) -> list[ComponentView]:
        flattened: list[ComponentView] = []
        for item in series.items:
            if isinstance(item, Leaf):
                flattened.append(item.component)
            elif isinstance(item, ParallelGroup):
                for branch in item.branches:
                    flattened.extend(MainWindow._flatten_series_components(branch))
        return flattened

    @staticmethod
    def _ast_to_text(node: ExprNode) -> str:
        if isinstance(node, ContactNode):
            return node.name
        if isinstance(node, NotNode):
            return f"NOT({MainWindow._ast_to_text(node.operand)})"
        if isinstance(node, AndNode):
            return " AND ".join(f"({MainWindow._ast_to_text(op)})" for op in node.operands)
        if isinstance(node, OrNode):
            return " OR ".join(f"({MainWindow._ast_to_text(op)})" for op in node.operands)
        return "UNKNOWN"

    @staticmethod
    def _normalize_expr(expr: str) -> str:
        text = expr.strip()
        # Accept common operator variants from preprocessed strings.
        text = re.sub(r"(?i)_+\s*OR\s*_+", " OR ", text)
        text = re.sub(r"(?i)_+\s*AND\s*_+", " AND ", text)
        text = text.replace("||", " OR ")
        text = text.replace("|", " OR ")
        text = text.replace("&&", " AND ")
        text = text.replace("&", " AND ")
        text = text.replace(",", " ")
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def merge_csv_files(self):
        il_paths = self.il_path.selected_paths()
        if not il_paths:
            QMessageBox.warning(self, "입력 확인", "병합할 IL CSV 파일을 먼저 추가하세요.")
            return

        initial_dir = os.path.dirname(il_paths[0]) if il_paths else ""
        default_name = "merged_il.csv"
        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "병합 CSV 저장",
            os.path.join(initial_dir, default_name),
            "CSV Files (*.csv);;All Files (*)",
        )
        if not output_path:
            return

        if not output_path.lower().endswith(".csv"):
            output_path = f"{output_path}.csv"

        try:
            processor = PLCPreprocessor()
            merged_df = processor.merge_il_csv_files(il_paths, output_path)
            self.merged_csv_path.set_paths([output_path])
            QMessageBox.information(
                self,
                "완료",
                f"{len(il_paths)}개 IL CSV를 병합해 저장했습니다.\n"
                f"저장 경로: {output_path}\n"
                f"병합 행 수: {len(merged_df)}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "오류", f"CSV 병합 중 오류가 발생했습니다.\n{exc}")

    @staticmethod
    def _resolve_input_path(widget, title: str) -> str:
        chosen = widget.latest_path_for_analysis()
        if not chosen:
            return ""
        paths = widget.selected_paths()
        if len(paths) > 1:
            QMessageBox.information(
                widget,
                "다중 파일 감지",
                f"{title}에 여러 CSV가 선택되었습니다.\n최근 드래그한 파일을 우선 분석에 사용합니다.",
            )
        return chosen

    def run_analysis(self):
        il_csv_path = ""
        merged_paths = self.merged_csv_path.selected_paths()
        if merged_paths:
            merged_candidate = merged_paths[-1]
            if os.path.exists(merged_candidate):
                il_csv_path = merged_candidate

        if not il_csv_path:
            il_paths = self.il_path.selected_paths()
            if il_paths:
                il_csv_path = il_paths[0]

        comment_csv_path = self._resolve_input_path(self.comment_path, "COMMENT CSV")

        if not il_csv_path or not comment_csv_path:
            QMessageBox.warning(self, "입력 확인", "IL CSV와 COMMENT CSV 경로를 모두 입력하세요.")
            return

        if not os.path.exists(il_csv_path) or not os.path.exists(comment_csv_path):
            QMessageBox.warning(self, "파일 확인", "입력한 CSV 파일 경로가 존재하지 않습니다.")
            return

        try:
            processor = PLCPreprocessor()
            self.out_df, self.comment_map, self.comment_table = processor.build_logic_with_comments(
                il_csv_path=il_csv_path,
                comment_csv_path=comment_csv_path,
            )

            tracer = ILInterlockTracer(out_df=self.out_df, comment_table=self.comment_table)
            cmd_coils = tracer.get_target_cmd_coils_in_out_df()

            if not cmd_coils:
                self.result_table.setRowCount(0)
                self.result_table.setColumnCount(0)
                self.result_label.setText("결과 테이블: 타겟 코일 없음")
                QMessageBox.information(self, "안내", "타겟 코일이 존재하지 않습니다.\n타겟 검출DB를 재확인하세요.")
                return

            self.cmd_df = (
                pd.DataFrame({"cmd_coil": cmd_coils})
                .sort_values("cmd_coil")
                .reset_index(drop=True)
            )

            cmd_table = self.comment_table[self.comment_table["device"].isin(cmd_coils)].copy()
            cmd_table = cmd_table.rename(columns={"device": "coil"})

            self.cmd_df_intable = pd.merge(
                cmd_table[["coil", "comment"]],
                self.out_df[["coil", "ins", "expr"]],
                on="coil",
                how="inner",
            ).sort_values("coil").reset_index(drop=True)

            self.comment_map = tracer.comment_map
            self.cmd_df_intable["expr2"] = self.cmd_df_intable["expr"].apply(
                lambda x: tracer.expr_to_comment_expr(x, self.comment_map)
            )

            results = tracer.trace_all_targets()
            trace_df = tracer.to_summary_table(results)
            if self.cmd_df_intable.empty or trace_df.empty:
                self.result_table.setRowCount(0)
                self.result_table.setColumnCount(0)
                self.result_label.setText("결과 테이블: 타겟 코일 없음")
                QMessageBox.information(self, "안내", "타겟 코일을 찾지 못했습니다.")
                return
            self.cmd_df_intable = self.cmd_df_intable.merge(trace_df, on="coil", how="left")

            self.populate_result_table(self.cmd_df_intable)
            self.result_label.setText(
                f"결과 테이블: out_df {len(self.out_df)}행, cmd_df_intable {len(self.cmd_df_intable)}행"
            )
            QMessageBox.information(self, "완료", "CSV 분석이 완료되었습니다.")

        except Exception as exc:
            self.result_table.setRowCount(0)
            self.result_table.setColumnCount(0)
            self.result_label.setText("결과 테이블: 실패")
            QMessageBox.critical(self, "오류", f"분석 중 오류가 발생했습니다.\n{exc}")

    def populate_result_table(self, df: pd.DataFrame):
        if df is None or df.empty:
            self.result_table.setRowCount(0)
            self.result_table.setColumnCount(0)
            return

        display_df = df.copy()
        self.result_table.setColumnCount(len(display_df.columns))
        self.result_table.setHorizontalHeaderLabels([str(c) for c in display_df.columns])
        self.result_table.setRowCount(len(display_df))

        for r in range(len(display_df)):
            for c, col in enumerate(display_df.columns):
                value = display_df.iloc[r, c]
                text = "" if pd.isna(value) else str(value)
                self.result_table.setItem(r, c, QTableWidgetItem(text))


def run_app(argv):
    app = QApplication(argv)
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(r"C:\Users\LGES\Desktop\AX_Proj\Labubu.png"))
    window = MainWindow()
    window.show()
    return app.exec()

