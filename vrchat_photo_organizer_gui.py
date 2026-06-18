import re
import json
import shutil
import zlib
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from datetime import datetime
from collections import defaultdict

IMAGE_EXTS = {".png"}

# Keep this False for speed.
# If everything goes into Unknown_World, change to True and try again.
SLOW_RAW_SCAN_FALLBACK = False


def safe_name(name):
    if not name:
        return "Unknown_World"
    name = re.sub(r'[\\/:*?"<>|]', "_", str(name)).strip()
    return name or "Unknown_World"


def extract_png_text_chunks(path):
    chunks = []

    with path.open("rb") as f:
        if f.read(8) != b"\x89PNG\r\n\x1a\n":
            return chunks

        while True:
            length_bytes = f.read(4)
            if len(length_bytes) < 4:
                break

            length = int.from_bytes(length_bytes, "big")
            chunk_type = f.read(4)

            if len(chunk_type) < 4:
                break

            if chunk_type in (b"tEXt", b"iTXt", b"zTXt"):
                chunk_data = f.read(length)

                if chunk_type in (b"tEXt", b"iTXt"):
                    try:
                        chunks.append(chunk_data.decode("utf-8", errors="ignore"))
                    except Exception:
                        pass

                elif chunk_type == b"zTXt":
                    try:
                        zero = chunk_data.find(b"\x00")
                        if zero != -1 and zero + 2 < len(chunk_data):
                            compressed = chunk_data[zero + 2:]
                            chunks.append(zlib.decompress(compressed).decode("utf-8", errors="ignore"))
                    except Exception:
                        pass
            else:
                f.seek(length, 1)

            # Skip CRC
            f.seek(4, 1)

            if chunk_type == b"IEND":
                break

    return chunks


def extract_possible_json_objects(text):
    results = []

    for start, ch in enumerate(text):
        if ch != "{":
            continue

        depth = 0
        in_str = False
        esc = False

        for i in range(start, len(text)):
            c = text[i]

            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1

                    if depth == 0:
                        candidate = text[start:i + 1]
                        if any(k in candidate for k in ("VRCX", "wrld_", "usr_", "players", "world_display_name", "world")):
                            results.append(candidate)
                        break

    return results


def try_load_metadata_from_text(text):
    for candidate in extract_possible_json_objects(text):
        try:
            data = json.loads(candidate)

            # Some metadata tools wrap the useful JSON inside "description".
            if isinstance(data, dict) and isinstance(data.get("description"), str):
                try:
                    nested = json.loads(data["description"])
                    if isinstance(nested, dict):
                        return nested
                except Exception:
                    pass

            if isinstance(data, dict):
                return data

        except Exception:
            continue

    return None


def get_metadata(path):
    if path.suffix.lower() != ".png":
        return None

    text = "\n".join(extract_png_text_chunks(path))
    meta = try_load_metadata_from_text(text)

    if meta:
        return meta

    if SLOW_RAW_SCAN_FALLBACK:
        try:
            raw_text = path.read_bytes().decode("utf-8", errors="ignore")
            return try_load_metadata_from_text(raw_text)
        except Exception:
            return None

    return None


def get_world_name(meta):
    if not isinstance(meta, dict):
        return "Unknown_World"

    if meta.get("world_display_name"):
        return safe_name(meta.get("world_display_name"))

    world = meta.get("world")
    if isinstance(world, dict):
        return safe_name(world.get("name") or world.get("displayName"))

    return "Unknown_World"


def get_date_string(path, meta):
    raw = None

    if isinstance(meta, dict):
        raw = meta.get("date_time") or meta.get("create_date") or meta.get("created_at")

    if raw:
        cleaned = str(raw).replace(":", "-", 2)
        try:
            return datetime.fromisoformat(cleaned).strftime("%Y-%m-%d")
        except Exception:
            pass

    # Fallback for VRChat-style filenames:
    # VRChat_2026-06-01_03-00-46.250_3840x2160.png
    m = re.search(r"VRChat_(\d{4}-\d{2}-\d{2})_", path.name)
    if m:
        return m.group(1)

    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def add_people(people, meta):
    if not isinstance(meta, dict):
        return

    author = meta.get("author")
    if isinstance(author, dict):
        name = author.get("displayName")
        uid = author.get("id")
        if name and uid:
            people[name] = uid

    players = meta.get("players")
    if isinstance(players, list):
        for p in players:
            if isinstance(p, dict):
                name = p.get("displayName")
                uid = p.get("id")
                if name and uid:
                    people[name] = uid


def unique_destination(dest):
    if not dest.exists():
        return dest

    stem = dest.stem
    suffix = dest.suffix
    parent = dest.parent
    i = 1

    while True:
        new_dest = parent / f"{stem} ({i}){suffix}"
        if not new_dest.exists():
            return new_dest
        i += 1


class PhotoOrganizerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("VRChat Photo Organizer")
        self.root.geometry("780x540")
        self.root.minsize(700, 470)

        self.folder_var = tk.StringVar()
        self.running = False

        self.build_ui()

    def build_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        title = ttk.Label(main, text="VRChat Photo Organizer", font=("Segoe UI", 16, "bold"))
        title.pack(anchor="w")

        desc = ttk.Label(
            main,
            text="Organises VRChat photos by World > Date using embedded VRChat/VRCX metadata. Photos with missing metadata go into one Miscellaneous Photos folder.",
            wraplength=720,
        )
        desc.pack(anchor="w", pady=(4, 14))

        folder_frame = ttk.Frame(main)
        folder_frame.pack(fill="x")

        ttk.Label(folder_frame, text="Folder:").pack(side="left")

        folder_entry = ttk.Entry(folder_frame, textvariable=self.folder_var)
        folder_entry.pack(side="left", fill="x", expand=True, padx=8)

        browse_btn = ttk.Button(folder_frame, text="Browse...", command=self.choose_folder)
        browse_btn.pack(side="left")

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=(12, 8))

        self.start_btn = ttk.Button(btn_frame, text="Start Organising", command=self.start_sorting)
        self.start_btn.pack(side="left")

        self.status_label = ttk.Label(btn_frame, text="Ready.")
        self.status_label.pack(side="left", padx=12)

        self.progress = ttk.Progressbar(main, mode="determinate")
        self.progress.pack(fill="x", pady=(4, 10))

        ttk.Label(main, text="Log:").pack(anchor="w")

        log_frame = ttk.Frame(main)
        log_frame.pack(fill="both", expand=True)

        self.log_box = tk.Text(log_frame, height=14, wrap="word")
        self.log_box.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_box.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_box.configure(yscrollcommand=scrollbar.set)

    def choose_folder(self):
        folder = filedialog.askdirectory(title="Choose VRChat photo folder")
        if folder:
            self.folder_var.set(folder)

    def log(self, msg):
        self.root.after(0, self._log_ui, msg)

    def _log_ui(self, msg):
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")

    def set_status(self, msg):
        self.root.after(0, lambda: self.status_label.configure(text=msg))

    def set_progress(self, current, total):
        def update():
            self.progress["maximum"] = max(total, 1)
            self.progress["value"] = current
        self.root.after(0, update)

    def start_sorting(self):
        if self.running:
            return

        folder = self.folder_var.get().strip()

        if not folder:
            messagebox.showwarning("No folder selected", "Please choose a folder first.")
            return

        path = Path(folder)

        if not path.exists() or not path.is_dir():
            messagebox.showerror("Invalid folder", "The selected folder does not exist.")
            return

        self.running = True
        self.start_btn.configure(state="disabled")
        self.log_box.delete("1.0", "end")
        self.progress["value"] = 0

        thread = threading.Thread(target=self.organise_folder, args=(path,), daemon=True)
        thread.start()

    def organise_folder(self, base):
        log_file = base / "sorter_log.txt"
        log_file.write_text(f"VRChat Photo Organizer Log - {datetime.now()}\n\n", encoding="utf-8")

        def file_log(msg):
            self.log(msg)
            with log_file.open("a", encoding="utf-8") as f:
                f.write(msg + "\n")

        try:
            files = [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
            total = len(files)

            file_log(f"Scanning folder: {base}")
            file_log(f"Found {total} PNG files.")

            if total == 0:
                self.set_status("No PNG files found.")
                return

            people_by_folder = defaultdict(dict)
            moved = 0
            unknown = 0
            errors = 0

            for index, path in enumerate(files, 1):
                try:
                    meta = get_metadata(path)
                    world_name = get_world_name(meta)
                    date_str = get_date_string(path, meta)

                    if world_name == "Unknown_World":
                        unknown += 1
                        target_folder = base / "Miscellaneous Photos"
                        log_target = "Miscellaneous Photos"
                    else:
                        target_folder = base / world_name / f"{date_str} - {world_name}"
                        log_target = f"{world_name}\\{date_str} - {world_name}"

                    target_folder.mkdir(parents=True, exist_ok=True)

                    add_people(people_by_folder[target_folder], meta)

                    dest = unique_destination(target_folder / path.name)
                    shutil.move(str(path), str(dest))
                    moved += 1

                    file_log(f"[{index}/{total}] Moved: {path.name} -> {log_target}")

                except Exception as e:
                    errors += 1
                    file_log(f"[{index}/{total}] ERROR processing {path.name}: {e}")

                self.set_progress(index, total)
                self.set_status(f"Processed {index}/{total}")

            for folder, people in people_by_folder.items():
                people_file = folder / "people.txt"

                lines = [
                    "People found in photos",
                    "======================",
                    "",
                    f"Folder: {folder}",
                    ""
                ]

                if people:
                    for name in sorted(people):
                        lines.append(f"{name} - {people[name]}")
                else:
                    lines.append("No player metadata found.")

                people_file.write_text("\n".join(lines), encoding="utf-8")
                file_log(f"Wrote: {people_file}")

            file_log("")
            file_log("Done.")
            file_log(f"Moved: {moved}")
            file_log(f"Miscellaneous photos: {unknown}")
            file_log(f"Errors: {errors}")

            self.set_status("Done.")
            self.root.after(
                0,
                lambda: messagebox.showinfo(
                    "Finished",
                    f"Organising complete.\n\nMoved: {moved}\nMiscellaneous photos: {unknown}\nErrors: {errors}"
                )
            )

        finally:
            self.running = False
            self.root.after(0, lambda: self.start_btn.configure(state="normal"))


def main():
    root = tk.Tk()
    PhotoOrganizerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
