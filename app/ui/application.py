from __future__ import annotations


from pathlib import Path
from dataclasses import asdict, fields

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QMessageBox,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from app.paths import default_tesseract_path, resource_path
from app.settings import SETTINGS_PATH, AppSettings, ensure_default_report_template, save_settings, validate_settings
from app.ui.main_window import MainWindow


def load_qss(settings: AppSettings) -> str:
    path = resource_path("app/ui/styles") / ("dark.qss" if settings.theme == "dark" else "light.qss")
    return path.read_text(encoding="utf-8") if path.exists() else ""


class FirstRunWizard(QWizard):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.setWindowTitle("Первичная настройка TimeLog OCR")
        self.setMinimumWidth(640)

        intro = QWizardPage()
        intro.setTitle("Добро пожаловать в TimeLog OCR")
        intro_layout = QVBoxLayout(intro)
        intro_layout.addWidget(QLabel(
            "Приложение выполняет OCR локально. Компонент распознавания уже "
            "встроен; выберите только папку для журналов рабочего дня."
        ))
        self.addPage(intro)

        config = QWizardPage()
        config.setTitle("Основные параметры")
        form = QFormLayout(config)
        self.base_directory = QLineEdit(settings.base_directory)
        self.theme = QComboBox()
        self.theme.addItems(["light", "dark"])
        self.theme.setCurrentText(settings.theme)
        self.start_minimized = QCheckBox("Запускать приложение свёрнутым")
        self.start_minimized.setChecked(settings.start_minimized)
        form.addRow("Распознавание текста:", QLabel("Готово — встроено в приложение" if default_tesseract_path().exists() else "Компонент будет проверен при запуске OCR"))
        form.addRow("Папка OCR-данных:", self._path_row(self.base_directory, file_mode=False))
        form.addRow("Тема:", self.theme)
        form.addRow("", self.start_minimized)
        self.addPage(config)

    def _path_row(self, edit: QLineEdit, *, file_mode: bool) -> QWidget:
        button = QPushButton("Выбрать…")

        def choose() -> None:
            current = edit.text() or str(Path.home())
            if file_mode:
                selected, _ = QFileDialog.getOpenFileName(self, "Выберите tesseract.exe", current, "Executable (*.exe);;All files (*.*)")
            else:
                selected = QFileDialog.getExistingDirectory(self, "Выберите папку OCR-данных", current)
            if selected:
                edit.setText(selected)

        button.clicked.connect(choose)
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit, 1)
        layout.addWidget(button)
        return container

    def apply_to(self, settings: AppSettings) -> None:
        settings.base_directory = self.base_directory.text().strip()
        settings.theme = self.theme.currentText()
        settings.start_minimized = self.start_minimized.isChecked()


def run_first_run_wizard(settings: AppSettings, settings_path: Path = SETTINGS_PATH) -> bool:
    wizard = FirstRunWizard(settings)
    while True:
        if wizard.exec() != QDialog.DialogCode.Accepted:
            return False
        candidate = AppSettings(**asdict(settings))
        wizard.apply_to(candidate)
        try:
            validate_settings(candidate, strict_paths=False)
            ensure_default_report_template(candidate)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(wizard, "TimeLog OCR", str(exc))
            continue
        candidate.first_run_completed = True
        save_settings(candidate, settings_path)
        for setting_field in fields(AppSettings):
            setattr(settings, setting_field.name, getattr(candidate, setting_field.name))
        return True


def create_main_window(app: QApplication, settings: AppSettings, start_minimized: bool = False) -> MainWindow:
    if not settings.first_run_completed:
        run_first_run_wizard(settings)
    app.setStyleSheet(load_qss(settings))
    return MainWindow(settings, start_minimized=start_minimized, settings_path=SETTINGS_PATH)
