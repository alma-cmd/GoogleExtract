import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext

from organizer import run_organizer


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Организатор Google Takeout")
        self.geometry("780x600")
        self.resizable(True, True)

        self.source_var = tk.StringVar()
        self.dest_var = tk.StringVar()
        self.delete_temp_var = tk.BooleanVar(value=True)
        self.unsorted_var = tk.BooleanVar(value=True)
        self.is_running = False

        self._build_ui()

    def _build_ui(self):
        # --- Источник ---
        frame_src = ttk.LabelFrame(self, text="Источник (ZIP-архив, папка с архивами или распакованная папка)")
        frame_src.pack(fill="x", padx=10, pady=(10, 5))

        ttk.Entry(frame_src, textvariable=self.source_var).pack(
            side="left", fill="x", expand=True, padx=5, pady=5)
        ttk.Button(frame_src, text="Выбрать...", command=self._choose_source).pack(
            side="right", padx=5, pady=5)

        # --- Назначение ---
        frame_dst = ttk.LabelFrame(self, text="Куда сохранить отсортированные файлы")
        frame_dst.pack(fill="x", padx=10, pady=5)

        ttk.Entry(frame_dst, textvariable=self.dest_var).pack(
            side="left", fill="x", expand=True, padx=5, pady=5)
        ttk.Button(frame_dst, text="Выбрать...", command=self._choose_dest).pack(
            side="right", padx=5, pady=5)

        # --- Опции ---
        frame_opts = ttk.LabelFrame(self, text="Опции")
        frame_opts.pack(fill="x", padx=10, pady=5)

        ttk.Checkbutton(
            frame_opts,
            text="Удалить временные файлы после обработки (освобождает место на диске)",
            variable=self.delete_temp_var
        ).pack(anchor="w", padx=10, pady=(5, 0))

        ttk.Checkbutton(
            frame_opts,
            text="Файлы с датой за текущий месяц или из будущего складывать в папку Unsorted",
            variable=self.unsorted_var
        ).pack(anchor="w", padx=10, pady=(0, 5))

        # --- Кнопка запуска ---
        self.btn_start = ttk.Button(self, text="▶ Начать обработку", command=self._start)
        self.btn_start.pack(pady=10)

        # --- Прогресс ---
        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=(0, 5))

        self.lbl_progress = ttk.Label(self, text="Готов к работе")
        self.lbl_progress.pack(pady=(0, 5))

        # --- Лог ---
        frame_log = ttk.LabelFrame(self, text="Лог")
        frame_log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.log_text = scrolledtext.ScrolledText(frame_log, height=14, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)

    # ---------- Выбор путей ----------

    def _choose_source(self):
        path = filedialog.askdirectory(title="Выберите папку с ZIP-архивами Google Takeout")
        if not path:
            path = filedialog.askopenfilename(
                title="Или выберите один ZIP-архив",
                filetypes=[("ZIP архивы", "*.zip"), ("Все файлы", "*.*")]
            )
        if path:
            self.source_var.set(path)

    def _choose_dest(self):
        path = filedialog.askdirectory(title="Выберите папку для сохранения")
        if path:
            self.dest_var.set(path)

    # ---------- Потокобезопасный вывод ----------

    def _log(self, msg):
        def _append():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(0, _append)

    def _update_progress(self, current, total):
        def _do():
            self.progress["maximum"] = total
            self.progress["value"] = current
            self.lbl_progress.config(text=f"Обработано: {current} из {total}")
        self.after(0, _do)

    # ---------- Запуск обработки ----------

    def _start(self):
        if self.is_running:
            return

        source = self.source_var.get().strip()
        dest = self.dest_var.get().strip()

        if not source or not dest:
            self._log("⚠️ Укажите путь к источнику и папку назначения.")
            return

        if not os.path.exists(source):
            self._log("❌ Путь к источнику не существует.")
            return

        delete_temp = self.delete_temp_var.get()
        use_unsorted = self.unsorted_var.get()

        self.is_running = True
        self.btn_start.config(state="disabled")
        self.progress["value"] = 0
        self.lbl_progress.config(text="Запуск...")
        self._log("\n" + "=" * 60)
        self._log("🚀 Начало обработки")

        def worker():
            try:
                run_organizer(
                    source, dest,
                    delete_temp=delete_temp,
                    use_unsorted=use_unsorted,
                    log_callback=self._log,
                    progress_callback=self._update_progress
                )
            except Exception as e:
                self._log(f"❌ Критическая ошибка: {e}")
            finally:
                self.after(0, self._on_finish)

        threading.Thread(target=worker, daemon=True).start()

    def _on_finish(self):
        self.is_running = False
        self.btn_start.config(state="normal")
        self.lbl_progress.config(text="Завершено")


if __name__ == "__main__":
    app = App()
    app.mainloop()