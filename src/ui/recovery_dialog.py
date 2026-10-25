"""Explicit recovery UI; text stays in RAM even when persistent history is off."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                               QListWidget, QPlainTextEdit, QPushButton, QVBoxLayout)


class RecoveryDialog(QDialog):
    def __init__(self, recovery, parent=None):
        super().__init__(parent)
        self.recovery = recovery
        self.setWindowTitle("Rio — Recent transcripts")
        self.resize(680, 520)
        self.setStyleSheet("""
            QDialog { background: #0C0C0E; color: #EDEDF3; }
            QLabel { color: #B4B4C0; padding: 4px; }
            QListWidget, QPlainTextEdit { background: #141418; color: #EDEDF3;
                border: 1px solid #30303A; border-radius: 6px; padding: 8px; }
            QListWidget::item:selected { background: #153C67; }
            QPushButton { background: #202026; color: #EDEDF3;
                border: 1px solid #383842; border-radius: 5px; padding: 8px 12px; }
            QPushButton:hover { background: #30303A; }
            QPushButton:disabled { color: #686874; }
        """)
        layout = QVBoxLayout(self)
        note = QLabel("The last 20 final transcripts stay in memory until cleared or Rio closes. "
                      "Copy a final transcript to recover missing words or review a live revision.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.reason = QLabel()
        self.reason.setWordWrap(True)
        layout.addWidget(self.reason)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text, 2)
        buttons = QHBoxLayout()
        self.copy = QPushButton("Copy final transcript")
        self.copy.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        buttons.addWidget(self.copy)
        clear = QPushButton("Clear recent transcripts")
        clear.clicked.connect(self._clear)
        buttons.addWidget(clear)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.list.currentRowChanged.connect(self._select)

    def refresh(self):
        self._entries = self.recovery.entries()
        self.list.clear()
        for entry in self._entries:
            # QListWidget items render plain text, including dictated markup.
            self.list.addItem(f"{entry.status} · {entry.text[:95]}")
        self.list.setCurrentRow(0 if self._entries else -1)
        self._select(self.list.currentRow())

    def _select(self, row):
        entry = self._entries[row] if 0 <= row < len(self._entries) else None
        self.text.setPlainText(entry.text if entry else "No final transcripts in this session.")
        self.reason.setTextFormat(Qt.TextFormat.PlainText)
        self.reason.setText(entry.reason if entry else "")
        self.copy.setEnabled(entry is not None)

    def _clear(self):
        self.recovery.clear()
        self.refresh()
