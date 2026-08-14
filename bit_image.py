import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageTk, ImageDraw
import numpy as np
import os
import struct
import requests

try:
    RESAMPLE_LANCZOS = Image.Resampling.LANCZOS
    RESAMPLE_NEAREST = Image.Resampling.NEAREST
except AttributeError:
    RESAMPLE_LANCZOS = Image.LANCZOS
    RESAMPLE_NEAREST = Image.NEAREST

THERMAL_DPI = 203
PRINT_WIDTH_MM = 48.0
PAPER_WIDTH_MM = 57.5

THERMAL_WIDTH_PX = round(PRINT_WIDTH_MM * THERMAL_DPI / 25.4)  # 384
PAPER_WIDTH_PX = round(PAPER_WIDTH_MM * THERMAL_DPI / 25.4)     # 460
LEFT_MARGIN_PX = max(0, (PAPER_WIDTH_PX - THERMAL_WIDTH_PX) // 2)

def apply_gamma(img_l, gamma):
    if gamma <= 0:
        gamma = 1.0
    inv = 1.0 / gamma
    lut = [min(255, max(0, int(((i / 255.0) ** inv) * 255.0 + 0.5))) for i in range(256)]
    return img_l.point(lut)

def ordered_bayer_dither(arr, threshold):
    bayer = np.array(
        [
            [0, 8, 2, 10],
            [12, 4, 14, 6],
            [3, 11, 1, 9],
            [15, 7, 13, 5],
        ],
        dtype=np.float32,
    )
    tile = np.tile(bayer, (arr.shape[0] // 4 + 1, arr.shape[1] // 4 + 1))
    tile = tile[: arr.shape[0], : arr.shape[1]]
    tmap = ((tile + 0.5) / 16.0) * 255.0
    tmap = np.clip(tmap + (threshold - 128), 0, 255)
    out = np.where(arr < tmap, 0, 255).astype(np.uint8)
    return out

def diffuse_error(arr, threshold, kernel, divisor, serpentine=True):
    work = arr.astype(np.float32).copy()
    h, w = work.shape

    for y in range(h):
        if serpentine and (y % 2 == 1):
            xs = range(w - 1, -1, -1)
            direction = -1
        else:
            xs = range(w)
            direction = 1

        for x in xs:
            old = work[y, x]
            new = 0.0 if old < threshold else 255.0
            err = old - new
            work[y, x] = new

            for dx, dy, weight in kernel:
                nx = x + (dx * direction)
                ny = y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    work[ny, nx] += err * (weight / divisor)

        work[y, :] = np.clip(work[y, :], 0, 255)

    return np.clip(work, 0, 255).astype(np.uint8)

def dither_image(img_l, method, threshold):
    arr = np.array(img_l, dtype=np.uint8)

    if method == "Threshold":
        out = np.where(arr < threshold, 0, 255).astype(np.uint8)

    elif method == "Ordered 4x4":
        out = ordered_bayer_dither(arr, threshold)

    elif method == "Floyd-Steinberg":
        kernel = [(1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1)]
        out = diffuse_error(arr, threshold, kernel, 16.0, serpentine=True)

    elif method == "Atkinson":
        kernel = [(1, 0, 1), (2, 0, 1), (-1, 1, 1), (0, 1, 1), (1, 1, 1), (0, 2, 1)]
        out = diffuse_error(arr, threshold, kernel, 8.0, serpentine=True)

    elif method == "Sierra Lite":
        kernel = [(1, 0, 2), (-1, 1, 1), (0, 1, 1)]
        out = diffuse_error(arr, threshold, kernel, 4.0, serpentine=True)

    elif method == "Sierra":
        kernel = [
            (1, 0, 5), (2, 0, 3),
            (-2, 1, 2), (-1, 1, 4), (0, 1, 5), (1, 1, 4), (2, 1, 2),
            (-1, 2, 2), (0, 2, 3), (1, 2, 2)
        ]
        out = diffuse_error(arr, threshold, kernel, 32.0, serpentine=True)

    elif method == "Stucki":
        kernel = [
            (1, 0, 8), (2, 0, 4),
            (-2, 1, 2), (-1, 1, 4), (0, 1, 8), (1, 1, 4), (2, 1, 2),
            (-2, 2, 1), (-1, 2, 2), (0, 2, 4), (1, 2, 2), (2, 2, 1)
        ]
        out = diffuse_error(arr, threshold, kernel, 42.0, serpentine=True)

    elif method == "Jarvis-Judice-Ninke":
        kernel = [
            (1, 0, 7), (2, 0, 5),
            (-2, 1, 3), (-1, 1, 5), (0, 1, 7), (1, 1, 5), (2, 1, 3),
            (-2, 2, 1), (-1, 2, 3), (0, 2, 5), (1, 2, 3), (2, 2, 1)
        ]
        out = diffuse_error(arr, threshold, kernel, 48.0, serpentine=True)

    else:
        out = np.where(arr < threshold, 0, 255).astype(np.uint8)

    return Image.fromarray(out, mode="L")

def pack_bitmap(img_l):
    img_l = img_l.convert("L")
    w, h = img_l.size
    width_bytes = (w + 7) // 8
    pixels = img_l.load()
    data = []

    for y in range(h):
        for xb in range(width_bytes):
            byte = 0
            for bit in range(8):
                x = xb * 8 + bit
                if x < w and pixels[x, y] < 128:
                    byte |= (1 << (7 - bit))
            data.append(byte)

    return width_bytes, h, bytes(data)

def make_paper_preview(img_1bit, zoom=2):
    img_1bit = img_1bit.convert("L")

    paper = Image.new("L", (PAPER_WIDTH_PX, img_1bit.height), 245)
    paper.paste(img_1bit, (LEFT_MARGIN_PX, 0))

    draw = ImageDraw.Draw(paper)
    draw.rectangle([0, 0, PAPER_WIDTH_PX - 1, img_1bit.height - 1], outline=210)
    draw.rectangle(
        [LEFT_MARGIN_PX, 0, LEFT_MARGIN_PX + THERMAL_WIDTH_PX - 1, img_1bit.height - 1],
        outline=180
    )

    zoom = max(1, int(zoom))
    preview = paper.resize((paper.width * zoom, paper.height * zoom), RESAMPLE_NEAREST)
    return preview

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("RP203 Thermal Image Generator")
        self.root.geometry("1450x900")

        self.original = None
        self.processed = None
        self.preview_photo = None

        self.method_var = tk.StringVar(value="Floyd-Steinberg")
        self.url_var = tk.StringVar(value="http://192.168.4.1")

        self.build_ui()

    def build_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="y", padx=(0, 10))

        right = ttk.Frame(main)
        right.pack(side="right", fill="both", expand=True)

        ttk.Label(left, text="ESP32 URL").pack(anchor="w")
        ttk.Entry(left, textvariable=self.url_var, width=30).pack(fill="x", pady=(0, 8))

        ttk.Button(left, text="Load Image", command=self.load_image).pack(fill="x", pady=3)
        ttk.Button(left, text="Export BIN", command=self.export_bin).pack(fill="x", pady=3)
        ttk.Button(left, text="Upload BIN to ESP32", command=self.upload_to_esp32).pack(fill="x", pady=3)
        ttk.Button(left, text="Save Preview PNG", command=self.save_preview_png).pack(fill="x", pady=3)

        ttk.Label(left, text="Dither Method").pack(anchor="w", pady=(10, 0))
        self.method_box = ttk.Combobox(
            left,
            values=[
                "Threshold",
                "Ordered 4x4",
                "Floyd-Steinberg",
                "Atkinson",
                "Sierra Lite",
                "Sierra",
                "Stucki",
                "Jarvis-Judice-Ninke",
            ],
            textvariable=self.method_var,
            state="readonly",
        )
        self.method_box.pack(fill="x")
        self.method_box.bind("<<ComboboxSelected>>", lambda e: self.update_preview())

        self.create_slider(left, "Brightness", 0.2, 3.0, 1.0)
        self.create_slider(left, "Contrast", 0.2, 4.0, 1.6)
        self.create_slider(left, "Sharpness", 0.0, 4.0, 2.0)
        self.create_slider(left, "Gamma", 0.4, 2.5, 0.95)
        self.create_slider(left, "Threshold", 0, 255, 128)
        self.create_slider(left, "Preview Zoom", 1, 8, 2)

        self.fit_var = tk.BooleanVar(value=True)
        self.autocontrast_var = tk.BooleanVar(value=True)
        self.median_var = tk.BooleanVar(value=True)
        self.sharpen_mask_var = tk.BooleanVar(value=True)
        self.invert_var = tk.BooleanVar(value=False)

        ttk.Checkbutton(left, text="Auto fit to 384 px", variable=self.fit_var, command=self.update_preview).pack(anchor="w")
        ttk.Checkbutton(left, text="Autocontrast", variable=self.autocontrast_var, command=self.update_preview).pack(anchor="w")
        ttk.Checkbutton(left, text="Median denoise", variable=self.median_var, command=self.update_preview).pack(anchor="w")
        ttk.Checkbutton(left, text="Unsharp mask", variable=self.sharpen_mask_var, command=self.update_preview).pack(anchor="w")
        ttk.Checkbutton(left, text="Invert", variable=self.invert_var, command=self.update_preview).pack(anchor="w")

        self.status = ttk.Label(left, text="Load an image to begin.", wraplength=260)
        self.status.pack(fill="x", pady=(15, 0))

        self.canvas = tk.Label(right, bd=2, relief="sunken", bg="gray20")
        self.canvas.pack(fill="both", expand=True)

    def create_slider(self, parent, label, frm, to, default):
        ttk.Label(parent, text=label).pack(anchor="w", pady=(8, 0))
        var = tk.DoubleVar(value=default)
        scale = tk.Scale(
            parent,
            from_=frm,
            to=to,
            orient="horizontal",
            resolution=0.1 if isinstance(default, float) else 1,
            variable=var,
            length=260,
            command=lambda e: self.update_preview(),
        )
        scale.pack(fill="x")
        setattr(self, f"{label.lower().replace(' ', '_')}_var", var)

    def load_image(self):
        path = filedialog.askopenfilename(
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff")]
        )
        if not path:
            return
        self.original = Image.open(path).convert("RGB")
        self.status.config(text=f"Loaded: {os.path.basename(path)}")
        self.update_preview()

    def process(self):
        if self.original is None:
            return None

        img = ImageOps.exif_transpose(self.original).convert("RGB")

        if self.fit_var.get():
            w, h = img.size
            new_h = max(1, int(h * (THERMAL_WIDTH_PX / w)))
            img = img.resize((THERMAL_WIDTH_PX, new_h), RESAMPLE_LANCZOS)

        img = img.convert("L")

        if self.autocontrast_var.get():
            img = ImageOps.autocontrast(img, cutoff=2)

        if self.median_var.get():
            img = img.filter(ImageFilter.MedianFilter(size=3))

        brightness = self.brightness_var.get()
        contrast = self.contrast_var.get()
        sharpness = self.sharpness_var.get()
        gamma = self.gamma_var.get()
        threshold = int(self.threshold_var.get())

        img = ImageEnhance.Brightness(img).enhance(brightness)
        img = ImageEnhance.Contrast(img).enhance(contrast)
        img = apply_gamma(img, gamma)

        if self.sharpen_mask_var.get():
            img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=170, threshold=2))
            img = ImageEnhance.Sharpness(img).enhance(sharpness)

        if self.invert_var.get():
            img = ImageOps.invert(img)

        return dither_image(img, self.method_var.get(), threshold)

    def update_preview(self):
        if self.original is None:
            return
        self.processed = self.process()
        if self.processed is None:
            return

        zoom = int(self.preview_zoom_var.get())
        preview = make_paper_preview(self.processed, zoom=zoom)
        self.preview_photo = ImageTk.PhotoImage(preview)
        self.canvas.config(image=self.preview_photo)

    def export_bin(self):
        if self.processed is None:
            messagebox.showwarning("No image", "Load and process an image first.")
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".bin",
            filetypes=[("Binary bitmap", "*.bin")],
            title="Save bitmap binary"
        )
        if not path:
            return

        bw, bh, data = pack_bitmap(self.processed)
        with open(path, "wb") as f:
            f.write(struct.pack("<HH", bw, bh))
            f.write(data)

        self.status.config(text=f"Saved BIN: {os.path.basename(path)}\n{bw} bytes/row, {bh} rows")

    def upload_to_esp32(self):
        if self.processed is None:
            messagebox.showwarning("No image", "Load and process an image first.")
            return

        url = self.url_var.get().strip().rstrip("/")
        if not url:
            messagebox.showwarning("No URL", "Enter ESP32 URL first.")
            return

        bw, bh, data = pack_bitmap(self.processed)
        payload = struct.pack("<HH", bw, bh) + data

        try:
            r = requests.post(
                f"{url}/upload",
                files={"imgfile": ("image.bin", payload, "application/octet-stream")},
                timeout=30,
            )
            r.raise_for_status()
            self.status.config(text=f"Uploaded to ESP32.\n{bw} bytes/row, {bh} rows")
            messagebox.showinfo("Upload OK", r.text)

        except Exception as e:
            messagebox.showerror("Upload failed", str(e))

    def save_preview_png(self):
        if self.processed is None:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png")],
            title="Save preview PNG"
        )
        if not path:
            return
        preview = make_paper_preview(self.processed, zoom=1)
        preview.save(path)
        messagebox.showinfo("Saved", "Preview PNG saved.")

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
