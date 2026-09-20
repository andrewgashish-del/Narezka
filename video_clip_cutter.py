#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Video Clip Cutter - Приложение для автоматической нарезки видеофайлов на Shorts/клипы по таймкодам.

ЗАВИСИМОСТИ:
1. Python 3.7+
2. FFmpeg должен быть установлен в системе:
   - Windows: скачайте с https://ffmpeg.org/download.html, распакуйте и добавьте папку bin в PATH
   - macOS: brew install ffmpeg
   - Linux: sudo apt install ffmpeg (Ubuntu/Debian) или sudo dnf install ffmpeg (Fedora)
3. Библиотека customtkinter (опционально, для красивого интерфейса):
   pip install customtkinter

ЗАПУСК:
   python video_clip_cutter.py

ИЛИ если установлен customtkinter:
   python video_clip_cutter.py
"""

import os
import re
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from pathlib import Path
from datetime import datetime
import subprocess
import shutil

# Попытка импортировать customtkinter для более современного интерфейса
try:
    import customtkinter as ctk
    USE_CUSTOMTKINTER = True
except ImportError:
    USE_CUSTOMTKINTER = False
    print("customtkinter не найден. Используем стандартный tkinter.")
    print("Установите: pip install customtkinter для улучшенного интерфейса")


class TimecodeParser:
    """Парсер таймкодов различных форматов."""
    
    # Паттерн для времени: HH:MM:SS или MM:SS или H:MM:SS и т.д.
    TIME_PATTERN = r'(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.(\d{3}))?'
    
    @staticmethod
    def parse_time_to_seconds(time_str: str) -> float:
        """
        Преобразует строку времени в секунды.
        Поддерживает форматы: HH:MM:SS.mmm, MM:SS.mmm, H:M:S, M:S и т.д.
        """
        time_str = time_str.strip()
        match = re.match(TimecodeParser.TIME_PATTERN, time_str)
        if not match:
            raise ValueError(f"Неверный формат времени: {time_str}")
        
        hours = int(match.group(1)) if match.group(1) else 0
        minutes = int(match.group(2))
        seconds = int(match.group(3)) if match.group(3) else 0
        milliseconds = int(match.group(4)) if match.group(4) else 0
        
        # Если часов не указано, но минут больше 59, считаем что это часы
        if match.group(3) is None and minutes > 59:
            hours = minutes
            minutes = seconds
            seconds = milliseconds
            milliseconds = 0
        
        total_seconds = hours * 3600 + minutes * 60 + seconds + milliseconds / 1000.0
        return total_seconds
    
    @staticmethod
    def sanitize_filename(name: str) -> str:
        """
        Очищает строку от недопустимых символов в именах файлов.
        Работает для Windows, macOS, Linux.
        """
        # Недопустимые символы для Windows: < > : " / \ | ? *
        # Также удаляем control characters
        invalid_chars = r'[<>:"/\\|？*\x00-\x1f\x7f]'
        sanitized = re.sub(invalid_chars, '_', name)
        # Заменяем множественные подчеркивания на одно
        sanitized = re.sub(r'_+', '_', sanitized)
        # Убираем подчеркивания в начале и конце
        sanitized = sanitized.strip('_')
        # Ограничиваем длину имени файла (максимум 200 символов для безопасности)
        if len(sanitized) > 200:
            sanitized = sanitized[:200]
        return sanitized if sanitized else "clip"
    
    @staticmethod
    def parse_line(line: str) -> dict:
        """
        Парсит одну строку с таймкодами.
        Возвращает словарь с start, end, name или None если строка невалидна.
        
        Поддерживаемые форматы:
        - 00:01:40 — 00:02:40 | Название сцены
        - 00:01:40 - 00:02:40
        - 01:40 - 02:40 Название сцены
        - 00:01:40-00:02:40 (без пробелов вокруг разделителя)
        """
        line = line.strip()
        if not line or line.startswith('#'):
            return None
        
        # Различные разделители: —, -, –
        # Ищем два таймкода разделенные любым из этих символов
        # Паттерн ищет: время1 [разделитель] время2 [опционально: | название]
        pattern = r'([\d:.]+)\s*[-–—]\s*([\d:.]+)(?:\s*[|]\s*(.+))?$'
        match = re.search(pattern, line)
        
        if not match:
            # Пробуем альтернативный паттерн без разделителя названия
            # В этом случае всё после времени считается названием
            pattern_alt = r'^([\d:.]+)\s*[-–—]\s*([\d:.]+)\s*(.*)$'
            match = re.match(pattern_alt, line)
            if match:
                start_str = match.group(1)
                end_str = match.group(2)
                # Всё что после времени - это название (если есть)
                name = match.group(3).strip() if match.group(3) else ""
            else:
                return None
        else:
            start_str = match.group(1)
            end_str = match.group(2)
            name = match.group(3).strip() if match.group(3) else ""
        
        try:
            start_seconds = TimecodeParser.parse_time_to_seconds(start_str)
            end_seconds = TimecodeParser.parse_time_to_seconds(end_str)
            
            if end_seconds <= start_seconds:
                return None
            
            sanitized_name = TimecodeParser.sanitize_filename(name)
            
            return {
                'start': start_seconds,
                'end': end_seconds,
                'name': sanitized_name,
                'original_name': name.strip() if name else None
            }
        except ValueError:
            return None


class VideoClipCutter:
    """Основной класс приложения для нарезки видео."""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Video Clip Cutter - Нарезка видео на Shorts")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)
        
        # Переменные
        self.video_file_path = tk.StringVar()
        self.output_folder_path = tk.StringVar()
        self.cut_mode = tk.StringVar(value="fast")  # fast или accurate
        self.crop_vertical = tk.BooleanVar(value=False)
        self.is_processing = False
        self.process_thread = None
        
        # Настройка стиля
        if USE_CUSTOMTKINTER:
            self._setup_customtkinter_style()
        else:
            self._setup_tkinter_style()
        
        self._create_widgets()
        self._check_ffmpeg()
    
    def _setup_customtkinter_style(self):
        """Настройка стиля для customtkinter."""
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.root.configure(bg="#1a1a2e")
        
        # Настройка цветов и шрифтов
        self.colors = {
            "bg_primary": "#1a1a2e",
            "bg_secondary": "#16213e",
            "bg_card": "#0f3460",
            "accent": "#e94560",
            "accent_hover": "#ff6b6b",
            "success": "#00d9a5",
            "success_hover": "#00f5c4",
            "text_primary": "#ffffff",
            "text_secondary": "#a0a0a0"
        }
    
    def _setup_tkinter_style(self):
        """Настройка стиля для стандартного tkinter."""
        self.root.configure(bg="#1a1a2e")
        # Настройка шрифтов
        self.font_normal = ("Arial", 11)
        self.font_bold = ("Arial", 12, "bold")
        self.font_large = ("Arial", 16, "bold")
        
        # Настройка цветов
        self.colors = {
            "bg_primary": "#1a1a2e",
            "bg_secondary": "#16213e",
            "bg_card": "#0f3460",
            "accent": "#e94560",
            "success": "#00d9a5",
            "text_primary": "#ffffff",
            "text_secondary": "#a0a0a0"
        }
    
    def _check_ffmpeg(self):
        """Проверяет наличие ffmpeg в системе."""
        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            self.ffmpeg_available = False
            # Откладываем показ предупреждения до создания виджетов
            self.root.after(100, self._show_ffmpeg_warning)
        else:
            self.ffmpeg_available = True
            self.ffmpeg_path = ffmpeg_path
    
    def _show_ffmpeg_warning(self):
        """Показывает предупреждение об отсутствии ffmpeg."""
        messagebox.showwarning(
            "FFmpeg не найден",
            "FFmpeg не обнаружен в системе!\n\n"
            "Пожалуйста, установите FFmpeg:\n"
            "• Windows: https://ffmpeg.org/download.html\n"
            "• macOS: brew install ffmpeg\n"
            "• Linux: sudo apt install ffmpeg\n\n"
            "После установки перезапустите приложение."
        )
    
    def _create_widgets(self):
        """Создает все виджеты интерфейса."""
        if USE_CUSTOMTKINTER:
            self._create_customtkinter_widgets()
        else:
            self._create_tkinter_widgets()
    
    def _create_tkinter_widgets(self):
        """Создает виджеты для стандартного tkinter."""
        # Основной фрейм с темным фоном
        main_frame = tk.Frame(self.root, bg=self.colors["bg_primary"], padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Заголовок приложения
        title_label = tk.Label(
            main_frame,
            text="🎬 Video Clip Cutter",
            font=("Arial", 20, "bold"),
            bg=self.colors["bg_primary"],
            fg=self.colors["accent"]
        )
        title_label.pack(pady=(0, 20))
        
        # Выбор видеофайла
        file_frame = tk.LabelFrame(
            main_frame, 
            text="📁 Исходный файл", 
            font=self.font_bold, 
            padx=15, 
            pady=15,
            bg=self.colors["bg_card"],
            fg=self.colors["text_primary"]
        )
        file_frame.pack(fill=tk.X, pady=(0, 15))
        
        tk.Label(file_frame, text="Видеофайл:", font=self.font_normal, bg=self.colors["bg_card"], fg=self.colors["text_primary"]).grid(row=0, column=0, sticky=tk.W)
        self.video_entry = tk.Entry(file_frame, textvariable=self.video_file_path, font=self.font_normal, width=60, bg="#1a1a2e", fg="#ffffff", insertbackground="#ffffff")
        self.video_entry.grid(row=0, column=1, padx=10, pady=5, sticky=tk.EW)
        tk.Button(
            file_frame, 
            text="📂 Выбрать видеофайл", 
            command=self.select_video_file, 
            font=self.font_normal,
            bg=self.colors["accent"],
            fg="white",
            activebackground=self.colors["accent_hover"],
            activeforeground="white",
            relief=tk.FLAT,
            padx=15,
            pady=8
        ).grid(row=0, column=2, padx=5)
        
        file_frame.columnconfigure(1, weight=1)
        
        # Папка сохранения
        save_frame = tk.LabelFrame(
            main_frame, 
            text="📂 Папка сохранения", 
            font=self.font_bold, 
            padx=15, 
            pady=15,
            bg=self.colors["bg_card"],
            fg=self.colors["text_primary"]
        )
        save_frame.pack(fill=tk.X, pady=(0, 15))
        
        tk.Label(save_frame, text="Папка:", font=self.font_normal, bg=self.colors["bg_card"], fg=self.colors["text_primary"]).grid(row=0, column=0, sticky=tk.W)
        self.output_entry = tk.Entry(save_frame, textvariable=self.output_folder_path, font=self.font_normal, width=60, bg="#1a1a2e", fg="#ffffff", insertbackground="#ffffff")
        self.output_entry.grid(row=0, column=1, padx=10, pady=5, sticky=tk.EW)
        tk.Button(
            save_frame, 
            text="📁 Выбрать папку", 
            command=self.select_output_folder, 
            font=self.font_normal,
            bg=self.colors["accent"],
            fg="white",
            activebackground=self.colors["accent_hover"],
            activeforeground="white",
            relief=tk.FLAT,
            padx=15,
            pady=8
        ).grid(row=0, column=2, padx=5)
        
        save_frame.columnconfigure(1, weight=1)
        
        # Настройки нарезки
        settings_frame = tk.LabelFrame(
            main_frame, 
            text="⚙️ Настройки нарезки", 
            font=self.font_bold, 
            padx=15, 
            pady=15,
            bg=self.colors["bg_card"],
            fg=self.colors["text_primary"]
        )
        settings_frame.pack(fill=tk.X, pady=(0, 15))
        
        # Режим нарезки
        mode_frame = tk.Frame(settings_frame, bg=self.colors["bg_card"])
        mode_frame.pack(fill=tk.X, pady=(0, 10))
        
        tk.Label(mode_frame, text="Режим:", font=self.font_normal, bg=self.colors["bg_card"], fg=self.colors["text_primary"]).pack(side=tk.LEFT)
        
        tk.Radiobutton(
            mode_frame, 
            text="⚡ Быстрая (-c copy)", 
            variable=self.cut_mode, 
            value="fast",
            font=self.font_normal,
            bg=self.colors["bg_card"],
            fg=self.colors["text_primary"],
            selectcolor=self.colors["bg_secondary"],
            activebackground=self.colors["bg_card"],
            activeforeground=self.colors["text_primary"]
        ).pack(side=tk.LEFT, padx=10)
        
        tk.Radiobutton(
            mode_frame,
            text="🎯 Точная (перекодирование)",
            variable=self.cut_mode,
            value="accurate",
            font=self.font_normal,
            bg=self.colors["bg_card"],
            fg=self.colors["text_primary"],
            selectcolor=self.colors["bg_secondary"],
            activebackground=self.colors["bg_card"],
            activeforeground=self.colors["text_primary"]
        ).pack(side=tk.LEFT, padx=10)
        
        # Чекбокс вертикального формата
        self.crop_checkbox = tk.Checkbutton(
            settings_frame,
            text="✂️ Обрезать под вертикальный формат 9:16 (1080x1920)",
            variable=self.crop_vertical,
            font=self.font_normal,
            bg=self.colors["bg_card"],
            fg=self.colors["text_primary"],
            selectcolor=self.colors["bg_secondary"],
            activebackground=self.colors["bg_card"],
            activeforeground=self.colors["text_primary"]
        )
        self.crop_checkbox.pack(anchor=tk.W, pady=(5, 0))
        
        # Поле для таймкодов
        timecode_frame = tk.LabelFrame(
            main_frame, 
            text="📝 Таймкоды (формат: 00:01:40 - 00:02:40 | Название)", 
            font=self.font_bold, 
            padx=15, 
            pady=15,
            bg=self.colors["bg_card"],
            fg=self.colors["text_primary"]
        )
        timecode_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
        
        self.timecode_text = scrolledtext.ScrolledText(
            timecode_frame, 
            font=("Consolas", 11),
            wrap=tk.WORD,
            height=15,
            bg="#1a1a2e",
            fg="#ffffff",
            insertbackground="#ffffff"
        )
        self.timecode_text.pack(fill=tk.BOTH, expand=True)
        
        # Кнопка вставки из буфера обмена
        paste_btn_frame = tk.Frame(timecode_frame, bg=self.colors["bg_card"])
        paste_btn_frame.pack(fill=tk.X, pady=(10, 0))
        
        tk.Button(
            paste_btn_frame,
            text="📋 Вставить таймкоды из буфера обмена",
            command=self.paste_timecodes_from_clipboard,
            font=self.font_normal,
            bg="#2196F3",
            fg="white",
            activebackground="#1976D2",
            activeforeground="white",
            relief=tk.FLAT,
            padx=20,
            pady=10
        ).pack(side=tk.LEFT)
        
        # Пример использования
        example_label = tk.Label(
            timecode_frame,
            text="Пример: 00:01:40 - 00:02:40 | Интересный момент",
            font=("Arial", 10),
            fg=self.colors["text_secondary"],
            bg=self.colors["bg_card"]
        )
        example_label.pack(anchor=tk.W, pady=(10, 0))
        
        # Кнопка начала и статус
        control_frame = tk.Frame(main_frame, bg=self.colors["bg_primary"])
        control_frame.pack(fill=tk.X, pady=(10, 0))
        
        self.start_button = tk.Button(
            control_frame,
            text="▶ НАЧАТЬ НАРЕЗКУ",
            command=self.start_cutting,
            font=self.font_large,
            bg=self.colors["success"],
            fg="white",
            activebackground=self.colors["success_hover"],
            activeforeground="white",
            relief=tk.FLAT,
            padx=30,
            pady=15
        )
        self.start_button.pack(side=tk.LEFT)
        
        # Строка статуса
        status_frame = tk.LabelFrame(
            main_frame, 
            text="📊 Лог процесса", 
            font=self.font_bold, 
            padx=15, 
            pady=15,
            bg=self.colors["bg_card"],
            fg=self.colors["text_primary"]
        )
        status_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        
        self.status_text = scrolledtext.ScrolledText(
            status_frame,
            font=("Consolas", 10),
            wrap=tk.WORD,
            height=8,
            state=tk.DISABLED,
            bg="#1a1a2e",
            fg="#00ff00",
            insertbackground="#00ff00"
        )
        self.status_text.pack(fill=tk.BOTH, expand=True)
    
    def _create_customtkinter_widgets(self):
        """Создает виджеты для customtkinter."""
        # Заголовок приложения
        title_label = ctk.CTkLabel(
            self.root,
            text="🎬 Video Clip Cutter",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color=self.colors["accent"]
        )
        title_label.pack(pady=(15, 10))
        
        # Скроллруемый фрейм для контента
        scrollable_frame = ctk.CTkScrollableFrame(self.root, fg_color=self.colors["bg_secondary"])
        scrollable_frame.pack(fill=tk.BOTH, expand=True, padx=25, pady=15)
        
        # Выбор видеофайла
        file_frame = ctk.CTkFrame(scrollable_frame, fg_color=self.colors["bg_card"])
        file_frame.pack(fill=tk.X, pady=(0, 15))
        
        ctk.CTkLabel(file_frame, text="📁 Исходный видеофайл:", font=ctk.CTkFont(size=14, weight="bold"), text_color=self.colors["text_primary"]).pack(anchor=tk.W, pady=(15, 10))
        
        file_select_frame = ctk.CTkFrame(file_frame, fg_color="transparent")
        file_select_frame.pack(fill=tk.X, padx=15, pady=(0, 15))
        
        self.video_entry = ctk.CTkEntry(
            file_select_frame, 
            textvariable=self.video_file_path, 
            placeholder_text="Выберите видеофайл...", 
            height=45,
            fg_color="#1a1a2e",
            border_color=self.colors["accent"],
            text_color=self.colors["text_primary"]
        )
        self.video_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        
        ctk.CTkButton(
            file_select_frame,
            text="📂 Выбрать видеофайл",
            command=self.select_video_file,
            height=45,
            fg_color=self.colors["accent"],
            hover_color=self.colors["accent_hover"],
            font=ctk.CTkFont(size=12)
        ).pack(side=tk.RIGHT)
        
        # Папка сохранения
        save_frame = ctk.CTkFrame(scrollable_frame, fg_color=self.colors["bg_card"])
        save_frame.pack(fill=tk.X, pady=(0, 15))
        
        ctk.CTkLabel(save_frame, text="📂 Папка сохранения:", font=ctk.CTkFont(size=14, weight="bold"), text_color=self.colors["text_primary"]).pack(anchor=tk.W, pady=(15, 10))
        
        save_select_frame = ctk.CTkFrame(save_frame, fg_color="transparent")
        save_select_frame.pack(fill=tk.X, padx=15, pady=(0, 15))
        
        self.output_entry = ctk.CTkEntry(
            save_select_frame, 
            textvariable=self.output_folder_path, 
            placeholder_text="По умолчанию - папка с видео", 
            height=45,
            fg_color="#1a1a2e",
            border_color=self.colors["accent"],
            text_color=self.colors["text_primary"]
        )
        self.output_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        
        ctk.CTkButton(
            save_select_frame,
            text="📁 Выбрать папку",
            command=self.select_output_folder,
            height=45,
            fg_color=self.colors["accent"],
            hover_color=self.colors["accent_hover"],
            font=ctk.CTkFont(size=12)
        ).pack(side=tk.RIGHT)
        
        # Настройки нарезки
        settings_frame = ctk.CTkFrame(scrollable_frame, fg_color=self.colors["bg_card"])
        settings_frame.pack(fill=tk.X, pady=(0, 15))
        
        ctk.CTkLabel(settings_frame, text="⚙️ Настройки нарезки:", font=ctk.CTkFont(size=14, weight="bold"), text_color=self.colors["text_primary"]).pack(anchor=tk.W, pady=(15, 10))
        
        # Режим нарезки
        mode_frame = ctk.CTkFrame(settings_frame, fg_color="transparent")
        mode_frame.pack(fill=tk.X, pady=(5, 15), padx=15)
        
        ctk.CTkLabel(mode_frame, text="Режим:", font=ctk.CTkFont(size=13), text_color=self.colors["text_primary"]).pack(side=tk.LEFT, padx=(0, 15))
        
        self.fast_radio = ctk.CTkRadioButton(
            mode_frame,
            text="⚡ Быстрая (-c copy)",
            variable=self.cut_mode,
            value="fast",
            fg_color=self.colors["accent"],
            hover_color=self.colors["accent_hover"],
            text_color=self.colors["text_primary"],
            font=ctk.CTkFont(size=12)
        )
        self.fast_radio.pack(side=tk.LEFT, padx=(0, 20))
        
        self.accurate_radio = ctk.CTkRadioButton(
            mode_frame,
            text="🎯 Точная (перекодирование)",
            variable=self.cut_mode,
            value="accurate",
            fg_color=self.colors["accent"],
            hover_color=self.colors["accent_hover"],
            text_color=self.colors["text_primary"],
            font=ctk.CTkFont(size=12)
        )
        self.accurate_radio.pack(side=tk.LEFT)
        
        # Чекбокс вертикального формата
        self.crop_checkbox = ctk.CTkCheckBox(
            settings_frame,
            text="✂️ Обрезать под вертикальный формат 9:16 (1080x1920)",
            fg_color=self.colors["accent"],
            hover_color=self.colors["accent_hover"],
            text_color=self.colors["text_primary"],
            font=ctk.CTkFont(size=12)
        )
        self.crop_checkbox.pack(anchor=tk.W, pady=(5, 15), padx=15)
        
        # Поле для таймкодов
        timecode_frame = ctk.CTkFrame(scrollable_frame, fg_color=self.colors["bg_card"])
        timecode_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
        
        ctk.CTkLabel(timecode_frame, text="📝 Таймкоды:", font=ctk.CTkFont(size=14, weight="bold"), text_color=self.colors["text_primary"]).pack(anchor=tk.W, pady=(15, 10), padx=15)
        
        self.timecode_text = ctk.CTkTextbox(
            timecode_frame,
            height=220,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color="#1a1a2e",
            border_color=self.colors["accent"],
            text_color=self.colors["text_primary"]
        )
        self.timecode_text.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 10))
        
        # Кнопка вставки из буфера обмена
        paste_btn_frame = ctk.CTkFrame(timecode_frame, fg_color="transparent")
        paste_btn_frame.pack(fill=tk.X, pady=(5, 10), padx=15)
        
        ctk.CTkButton(
            paste_btn_frame,
            text="📋 Вставить таймкоды из буфера обмена",
            command=self.paste_timecodes_from_clipboard,
            height=40,
            fg_color="#2196F3",
            hover_color="#1976D2",
            font=ctk.CTkFont(size=12)
        ).pack(side=tk.LEFT)
        
        # Пример
        ctk.CTkLabel(
            timecode_frame,
            text="Пример: 00:01:40 - 00:02:40 | Интересный момент",
            text_color=self.colors["text_secondary"],
            font=ctk.CTkFont(size=11)
        ).pack(anchor=tk.W, pady=(5, 15), padx=15)
        
        # Кнопка начала
        self.start_button = ctk.CTkButton(
            scrollable_frame,
            text="▶ НАЧАТЬ НАРЕЗКУ",
            command=self.start_cutting,
            height=55,
            font=ctk.CTkFont(size=18, weight="bold"),
            fg_color=self.colors["success"],
            hover_color=self.colors["success_hover"]
        )
        self.start_button.pack(fill=tk.X, pady=(10, 15), padx=15)
        
        # Статус
        status_frame = ctk.CTkFrame(scrollable_frame, fg_color=self.colors["bg_card"])
        status_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
        
        ctk.CTkLabel(status_frame, text="📊 Лог процесса:", font=ctk.CTkFont(size=14, weight="bold"), text_color=self.colors["text_primary"]).pack(anchor=tk.W, pady=(15, 10), padx=15)
        
        self.status_text = ctk.CTkTextbox(
            status_frame,
            height=170,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color="#1a1a2e",
            border_color=self.colors["accent"],
            text_color="#00ff00"
        )
        self.status_text.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 15))
    
    def select_video_file(self):
        """Открывает диалог выбора видеофайла."""
        filetypes = [
            ("Видеофайлы", "*.mp4 *.mkv *.avi *.mov *.webm *.flv"),
            ("MP4 файлы", "*.mp4"),
            ("MKV файлы", "*.mkv"),
            ("AVI файлы", "*.avi"),
            ("MOV файлы", "*.mov"),
            ("Все файлы", "*.*")
        ]
        
        filename = filedialog.askopenfilename(
            title="Выберите видеофайл",
            filetypes=filetypes
        )
        
        if filename:
            self.video_file_path.set(filename)
            # Автоматически устанавливаем папку сохранения как папку с видео
            if not self.output_folder_path.get():
                output_folder = os.path.dirname(filename)
                self.output_folder_path.set(output_folder)
            self.log_message(f"Выбран файл: {filename}")
    
    def paste_timecodes_from_clipboard(self):
        """Вставляет таймкоды из буфера обмена в текстовое поле."""
        try:
            clipboard_content = self.root.clipboard_get()
            if clipboard_content:
                # Получаем текущий контент и добавляем новый
                current_content = self.timecode_text.get("1.0", tk.END).strip()
                if current_content:
                    new_content = current_content + "\n" + clipboard_content.strip()
                else:
                    new_content = clipboard_content.strip()
                
                self.timecode_text.delete("1.0", tk.END)
                self.timecode_text.insert("1.0", new_content)
                self.log_message("Таймкоды вставлены из буфера обмена")
        except tk.TclError:
            # Буфер обмена пуст или недоступен
            messagebox.showwarning("Буфер обмена", "Буфер обмена пуст или недоступен")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось вставить таймкоды: {str(e)}")
    
    def select_output_folder(self):
        """Открывает диалог выбора папки сохранения."""
        folder = filedialog.askdirectory(
            title="Выберите папку для сохранения клипов"
        )
        
        if folder:
            self.output_folder_path.set(folder)
            self.log_message(f"Папка сохранения: {folder}")
    
    def log_message(self, message: str):
        """Добавляет сообщение в лог статуса."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}\n"
        
        if USE_CUSTOMTKINTER:
            self.status_text.configure(state=tk.NORMAL)
            self.status_text.insert(tk.END, log_entry)
            self.status_text.see(tk.END)
            self.status_text.configure(state=tk.DISABLED)
        else:
            self.status_text.configure(state=tk.NORMAL)
            self.status_text.insert(tk.END, log_entry)
            self.status_text.see(tk.END)
            self.status_text.configure(state=tk.DISABLED)
    
    def update_status(self):
        """Обновляет статус в зависимости от выбранного режима."""
        if self.cut_mode.get() == "fast":
            self.log_message("Режим: Быстрая нарезка (без перекодирования)")
        else:
            self.log_message("Режим: Точная нарезка (с перекодированием)")
    
    def start_cutting(self):
        """Запускает процесс нарезки в отдельном потоке."""
        if self.is_processing:
            messagebox.showwarning("Предупреждение", "Процесс нарезки уже запущен!")
            return
        
        # Валидация входных данных
        if not self.video_file_path.get():
            messagebox.showerror("Ошибка", "Пожалуйста, выберите видеофайл!")
            return
        
        if not os.path.exists(self.video_file_path.get()):
            messagebox.showerror("Ошибка", "Выбранный файл не существует!")
            return
        
        if not self.output_folder_path.get():
            messagebox.showerror("Ошибка", "Пожалуйста, выберите папку для сохранения!")
            return
        
        if not os.path.exists(self.output_folder_path.get()):
            messagebox.showerror("Ошибка", "Указанная папка не существует!")
            return
        
        if not self.ffmpeg_available:
            messagebox.showerror("Ошибка", "FFmpeg не найден! Пожалуйста, установите FFmpeg.")
            return
        
        # Получаем таймкоды
        timecodes_text = self.timecode_text.get("1.0", tk.END).strip()
        if not timecodes_text:
            messagebox.showerror("Ошибка", "Пожалуйста, введите таймкоды!")
            return
        
        # Парсим таймкоды
        timecodes = []
        for line in timecodes_text.split('\n'):
            parsed = TimecodeParser.parse_line(line)
            if parsed:
                timecodes.append(parsed)
        
        if not timecodes:
            messagebox.showerror("Ошибка", "Не найдено корректных таймкодов! Проверьте формат ввода.")
            return
        
        # Подтверждение
        confirm = messagebox.askyesno(
            "Подтверждение",
            f"Найдено {len(timecodes)} корректных таймкодов.\n"
            f"Начать нарезку?"
        )
        
        if not confirm:
            return
        
        # Запускаем в отдельном потоке
        self.is_processing = True
        self.start_button.configure(state=tk.DISABLED if not USE_CUSTOMTKINTER else {"state": "disabled"})
        
        self.process_thread = threading.Thread(
            target=self._cutting_process,
            args=(timecodes,),
            daemon=True
        )
        self.process_thread.start()
    
    def _cutting_process(self, timecodes: list):
        """Основной процесс нарезки (выполняется в отдельном потоке)."""
        video_path = self.video_file_path.get()
        output_folder = self.output_folder_path.get()
        mode = self.cut_mode.get()
        crop = self.crop_vertical.get() if hasattr(self.crop_vertical, 'get') else self.crop_checkbox.get()
        
        self.log_message("=" * 60)
        self.log_message(f"Начало нарезки: {os.path.basename(video_path)}")
        self.log_message(f"Режим: {'Быстрый' if mode == 'fast' else 'Точный'}")
        self.log_message(f"Вертикальный формат: {'Да' if crop else 'Нет'}")
        self.log_message("=" * 60)
        
        successful = 0
        failed = 0
        skipped = 0
        
        for i, tc in enumerate(timecodes, 1):
            if not self.is_processing:
                self.log_message("Процесс остановлен пользователем")
                break
            
            clip_num = f"{i:02d}"
            
            # Формируем имя файла
            if tc['name']:
                output_filename = f"clip_{clip_num}_{tc['name']}.mp4"
            else:
                output_filename = f"clip_{clip_num}.mp4"
            
            output_path = os.path.join(output_folder, output_filename)
            
            # Проверяем, не существует ли уже файл
            if os.path.exists(output_path):
                self.log_message(f"Файл {output_filename} уже существует, пропускаем...")
                skipped += 1
                continue
            
            self.log_message(f"\n[{i}/{len(timecodes)}] Обработка: {tc['original_name'] or f'Клип {clip_num}'}")
            self.log_message(f"  Время: {tc['start']:.2f}s - {tc['end']:.2f}s")
            self.log_message(f"  Выходной файл: {output_filename}")
            
            # Формируем команду ffmpeg
            cmd = self._build_ffmpeg_command(video_path, output_path, tc, mode, crop)
            
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=3600  # Таймаут 1 час на один клип
                )
                
                if result.returncode == 0:
                    self.log_message(f"  ✓ Успешно создано: {output_filename}")
                    successful += 1
                else:
                    self.log_message(f"  ✗ Ошибка: {result.stderr[:200]}")
                    failed += 1
                    
            except subprocess.TimeoutExpired:
                self.log_message(f"  ✗ Превышено время ожидания для клипа {clip_num}")
                failed += 1
            except Exception as e:
                self.log_message(f"  ✗ Исключение: {str(e)}")
                failed += 1
        
        # Итоги
        self.log_message("\n" + "=" * 60)
        self.log_message("НАРЕЗКА ЗАВЕРШЕНА")
        self.log_message(f"Всего клипов: {len(timecodes)}")
        self.log_message(f"Успешно: {successful}")
        self.log_message(f"Пропущено: {skipped}")
        self.log_message(f"Ошибок: {failed}")
        self.log_message("=" * 60)
        
        # Обновляем UI в главном потоке
        self.root.after(0, self._on_cutting_complete, successful, failed)
    
    def _build_ffmpeg_command(self, input_path: str, output_path: str, timecode: dict, mode: str, crop: bool) -> list:
        """
        Строит команду ffmpeg для нарезки.
        
        Args:
            input_path: Путь к исходному файлу
            output_path: Путь к выходному файлу
            timecode: Словарь с start, end
            mode: 'fast' или 'accurate'
            crop: True для вертикального формата 9:16
        """
        start = timecode['start']
        duration = timecode['end'] - timecode['start']
        
        cmd = ["ffmpeg", "-y"]  # -y для перезаписи файла
        
        if mode == "accurate":
            # Точная нарезка: сначала обрезаем, потом кодируем
            cmd.extend(["-ss", str(start)])
            cmd.extend(["-i", input_path])
            cmd.extend(["-t", str(duration)])
        else:
            # Быстрая нарезка: сначала ищем ключевой кадр, потом точно обрезаем
            cmd.extend(["-ss", str(start)])
            cmd.extend(["-i", input_path])
            cmd.extend(["-t", str(duration)])
            cmd.extend(["-c", "copy"])
        
        # Добавляем фильтры для вертикального формата
        if crop:
            # Центрированный кроп 1080x1920
            filter_complex = "crop=min(iw\\,ih*(9/16)):min(ih\\,iw*(16/9)):(iw-ow)/2:(ih-oh)/2,scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
            cmd.extend(["-vf", filter_complex])
        
        # Кодеки для точного режима
        if mode == "accurate":
            cmd.extend([
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "192k"
            ])
        
        cmd.append(output_path)
        
        return cmd
    
    def _on_cutting_complete(self, successful: int, failed: int):
        """Вызывается после завершения нарезки в главном потоке."""
        self.is_processing = False
        self.start_button.configure(state=tk.NORMAL if not USE_CUSTOMTKINTER else {"state": "normal"})
        
        if failed == 0:
            messagebox.showinfo(
                "Завершено",
                f"Нарезка успешно завершена!\n"
                f"Создано клипов: {successful}"
            )
        else:
            messagebox.showwarning(
                "Завершено с ошибками",
                f"Нарезка завершена.\n"
                f"Успешно: {successful}\n"
                f"Ошибок: {failed}\n\n"
                f"Проверьте лог для деталей."
            )
    
    def stop_cutting(self):
        """Останавливает процесс нарезки."""
        self.is_processing = False
        self.log_message("Попытка остановки процесса...")


def main():
    """Точка входа в приложение."""
    root = tk.Tk() if not USE_CUSTOMTKINTER else ctk.CTk()
    
    app = VideoClipCutter(root)
    
    # Обработка закрытия окна
    def on_closing():
        if app.is_processing:
            if messagebox.askokcancel("Выход", "Процесс нарезки выполняется. Все равно выйти?"):
                app.is_processing = False
                root.destroy()
        else:
            root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
