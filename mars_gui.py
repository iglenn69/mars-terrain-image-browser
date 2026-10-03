"""Desktop browser for NASA Mastcam-Z science products."""
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from dataclasses import asdict
import webbrowser
from mars_pds import (BUNDLES, Cancelled, discover, acquire, find_preview,
                      resolve, download, save_json)

class Browser(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Mars Terrain • PDS Image Browser')
        self.geometry('1320x850')
        self.minsize(1050, 700)
        self.configure(bg='#111827')
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.products = []
        self.preview_image = None
        self.sol = tk.StringVar(value='693')
        self.sequence = tk.StringVar(value='ZCAM08689')
        self.source = tk.StringVar(value='sci_calibrated')
        self.camera = tk.StringVar(value='Both')
        self.kind = tk.StringVar(value='RAD')
        self.full = tk.BooleanVar(value=True)
        self.convert = tk.BooleanVar(value=True)
        self.output = tk.StringVar(value=str(Path('downloads').resolve()))
        self.filter_text = tk.StringVar()
        self.status = tk.StringVar(value='Search the archive to find original PDS image products.')
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TFrame', background='#111827')
        style.configure('TLabel', background='#111827', foreground='#e5e7eb', font=('Segoe UI', 10))
        style.configure('Title.TLabel', font=('Segoe UI', 23, 'bold'))
        style.configure('TCheckbutton', background='#111827', foreground='#e5e7eb')
        style.configure('TButton', padding=7, font=('Segoe UI', 10))
        style.configure('Treeview', rowheight=29, font=('Segoe UI', 9))
        style.configure('Treeview.Heading', font=('Segoe UI', 9, 'bold'))
        shell = ttk.Frame(self, padding=20)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text='MARS TERRAIN', style='Title.TLabel').pack(anchor='w')
        ttk.Label(shell, text='NASA PDS  /  Mastcam-Z  /  Original images + camera metadata').pack(anchor='w', pady=(3, 15))
        search = ttk.Frame(shell)
        search.pack(fill='x')
        self.search_controls = []
        for title, var, width, choices in [
            ('Sol', self.sol, 7, None), ('Sequence (blank = all)', self.sequence, 18, None),
            ('Collection', self.source, 20, list(BUNDLES)), ('Camera', self.camera, 8, ['Both', 'ZL', 'ZR']),
            ('Product', self.kind, 8, ['RAD', 'IOF', 'EDR', 'ECM', 'All'])]:
            cell = ttk.Frame(search)
            cell.pack(side='left', padx=(0, 12))
            ttk.Label(cell, text=title).pack(anchor='w')
            widget = ttk.Combobox(cell, textvariable=var, width=width, values=choices, state='readonly') if choices else ttk.Entry(cell, textvariable=var, width=width)
            widget.pack(pady=5)
            self.search_controls.append(widget)
        self.search_button = ttk.Button(search, text='Search PDS', command=self.search)
        self.search_button.pack(side='left', anchor='s', pady=5)
        ttk.Checkbutton(shell, text='Full resolution only • exclude thumbnails and downsampling', variable=self.full).pack(anchor='w', pady=(4, 12))
        toolbar = ttk.Frame(shell)
        toolbar.pack(fill='x', pady=(0, 8))
        ttk.Label(toolbar, text='Filter results').pack(side='left', padx=(0, 10))
        ttk.Entry(toolbar, textvariable=self.filter_text, width=32).pack(side='left')
        self.filter_text.trace_add('write', lambda *_: self.populate())
        ttk.Button(toolbar, text='Select visible', command=lambda: self.table.selection_set(self.table.get_children())).pack(side='left', padx=10)
        ttk.Button(toolbar, text='Export catalogue', command=self.export).pack(side='left')
        ttk.Button(toolbar, text='Load catalogue', command=self.load_catalogue).pack(side='left', padx=8)
        self.count = ttk.Label(toolbar, text='0 products')
        self.count.pack(side='right')
        panels = ttk.Panedwindow(shell, orient='horizontal')
        panels.pack(fill='both', expand=True)
        left = ttk.Frame(panels)
        right = ttk.Frame(panels, padding=(15, 0, 0, 0))
        panels.add(left, weight=3)
        panels.add(right, weight=2)
        columns = ['camera', 'type', 'focal', 'compression', 'size', 'name']
        self.table = ttk.Treeview(left, columns=columns, show='headings', selectmode='extended')
        for col, title, width in zip(columns, ['Eye', 'Type', 'mm', 'Comp.', 'MiB', 'Product filename'], [45, 50, 45, 55, 65, 450]):
            self.table.heading(col, text=title)
            self.table.column(col, width=width, minwidth=40, stretch=col == 'name')
        ys = ttk.Scrollbar(left, orient='vertical', command=self.table.yview)
        xs = ttk.Scrollbar(left, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.table.grid(row=0, column=0, sticky='nsew')
        ys.grid(row=0, column=1, sticky='ns')
        xs.grid(row=1, column=0, sticky='ew')
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.table.bind('<<TreeviewSelect>>', self.selection_changed)
        ttk.Label(right, text='PRODUCT INSPECTOR', font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        self.preview = ttk.Label(right, text='Select an image and load its browse preview.', anchor='center')
        self.preview.pack(fill='both', expand=True, pady=10)
        ttk.Button(right, text='Load preview', command=self.load_preview).pack(anchor='w')
        self.details = tk.Text(right, height=8, wrap='word', bg='#1f2937', fg='#e5e7eb', font=('Consolas', 9), relief='flat')
        self.details.pack(fill='x', pady=10)
        self.details.configure(state='disabled')
        destination = ttk.Frame(shell)
        destination.pack(fill='x', pady=(15, 6))
        ttk.Label(destination, text='Download folder').pack(side='left', padx=(0, 10))
        ttk.Entry(destination, textvariable=self.output).pack(side='left', fill='x', expand=True)
        ttk.Button(destination, text='Browse…', command=self.choose_folder).pack(side='left', padx=8)
        actions = ttk.Frame(shell)
        actions.pack(fill='x', pady=5)
        ttk.Checkbutton(actions, text='Create 16-bit TIFF + scientific float TIFF', variable=self.convert).pack(side='left')
        self.download_button = ttk.Button(actions, text='Download selected', command=self.download_selected)
        self.download_button.pack(side='right')
        self.cancel_button = ttk.Button(actions, text='Cancel', command=self.cancel.set, state='disabled')
        self.cancel_button.pack(side='right', padx=8)
        self.progress = ttk.Progressbar(shell, maximum=100)
        self.progress.pack(fill='x', pady=6)
        ttk.Label(shell, textvariable=self.status, wraplength=1200).pack(anchor='w')
        self.log = tk.Text(shell, height=4, bg='#0b1220', fg='#cbd5e1', relief='flat', font=('Consolas', 9), state='disabled')
        self.log.pack(fill='x', pady=(8, 0))
        self.source.trace_add('write', self.source_changed)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.poll)

    def source_changed(self, *_):
        self.kind.set('RAD' if self.source.get() == 'sci_calibrated' else 'All')

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output.get())
        if folder:
            self.output.set(folder)

    def append_log(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def work(self, task):
        if self.busy:
            return
        self.busy = True
        self.cancel.clear()
        self.search_button.configure(state='disabled')
        self.download_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.progress['value'] = 0
        def run():
            try:
                task()
            except Cancelled:
                self.events.put(('message', 'Cancelled. Completed downloads remain available.'))
            except Exception as exc:
                self.events.put(('failure', str(exc)))
            finally:
                self.events.put(('idle',))
        threading.Thread(target=run, daemon=True).start()

    def search(self):
        try:
            sol = int(self.sol.get())
        except ValueError:
            messagebox.showerror('Invalid sol', 'Enter a whole sol number.')
            return
        source, sequence, camera, full, kind = self.source.get(), self.sequence.get(), self.camera.get(), self.full.get(), self.kind.get()
        self.status.set('Searching NASA’s catalogue…')
        def task():
            result = discover(sol, sequence, source, camera, full, kind=kind,
                progress=lambda *e: self.events.put(e), cancel=self.cancel)
            self.events.put(('results', result))
        self.work(task)

    def populate(self):
        selected = set(self.table.selection())
        self.table.delete(*self.table.get_children())
        term = self.filter_text.get().upper()
        for index, p in enumerate(self.products):
            if term and term not in p.name.upper():
                continue
            self.table.insert('', 'end', iid=str(index), values=(p.camera, p.kind, p.focal_mm,
                p.compression, f'{p.image_size/1024**2:.1f}', p.name))
        self.table.selection_set([i for i in selected if self.table.exists(i)])
        self.count.configure(text=f'{len(self.table.get_children())} visible / {len(self.products)} products')

    def selection_changed(self, *_):
        selected = self.table.selection()
        if not selected:
            return
        p = self.products[int(selected[0])]
        text = f'{p.name}\n\n{p.camera} • {p.kind} • {p.focal_mm} mm\nCompression code: {p.compression}\nFull resolution: {p.full_resolution}\nArchive bytes: {p.image_size:,}\nVersion: {p.version}\nSelected: {len(selected)}\n\nBrowse previews are for inspection. Download originals for reconstruction.'
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', text)
        self.details.configure(state='disabled')

    def export(self):
        if not self.products:
            return
        filename = filedialog.asksaveasfilename(defaultextension='.json', initialfile='catalogue.json')
        if filename:
            save_json(filename, [asdict(p) for p in self.products])
            self.append_log('Exported ' + filename)

    def load_preview(self):
        selected = self.table.selection()
        if not selected or self.busy:
            return
        p = self.products[int(selected[0])]
        output = Path(self.output.get())
        def task():
            e = find_preview(p)
            path = output / '.previews' / e['archive']['name']
            download(resolve(e['uri']), path, e['archive'].get('size', 0), e['archive'].get('md5', ''), cancel=self.cancel)
            self.events.put(('preview', path))
        self.work(task)

    def load_catalogue(self):
        if self.busy:
            return
        filename = filedialog.askopenfilename(filetypes=[('Catalogue JSON', '*.json')])
        if filename:
            try:
                from mars_pds import Product
                self.products = [Product(**p) for p in json.loads(Path(filename).read_text(encoding='utf-8'))]
                self.populate()
                self.status.set(f'Loaded {len(self.products)} saved products.')
            except Exception as exc:
                messagebox.showerror('Cannot load catalogue', str(exc))

    def download_selected(self):
        selected = self.table.selection()
        if not selected:
            messagebox.showinfo('Select products', 'Select rows, or use Select visible to download the filtered results.')
            return
        products = [self.products[int(i)] for i in selected]
        output, convert = self.output.get(), self.convert.get()
        def task():
            records = acquire(products, output, convert, progress=lambda *e: self.events.put(e), cancel=self.cancel)
            failed = sum(bool(r.get('error')) for r in records if r['source_uri'] in {p.uri for p in products})
            self.events.put(('message', f'Finished {len(products)} products; {failed} errors. Manifest: {Path(output) / "realitycapture_manifest.csv"}'))
        self.work(task)

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                tag = event[0]
                if tag == 'idle':
                    self.busy = False
                    self.search_button.configure(state='normal')
                    self.download_button.configure(state='normal')
                    self.cancel_button.configure(state='disabled')
                elif tag == 'results':
                    self.products = event[1]
                    self.populate()
                    self.status.set(f'Found {len(self.products)} products. Select rows to download originals and XML labels.')
                    self.append_log(self.status.get())
                elif tag == 'search':
                    self.status.set(f'Catalogue: {event[1]:,} / {event[2]:,} archive entries')
                elif tag == 'bytes':
                    self.progress['value'] = event[1] / event[2] * 100 if event[2] else 0
                elif tag == 'product':
                    self.status.set(f'Product {event[1]} / {event[2]}: {event[3]}')
                    self.append_log(self.status.get())
                elif tag in ('error', 'failure'):
                    text = ': '.join(str(s) for s in event[1:])
                    self.append_log('ERROR: ' + text)
                    self.status.set(text)
                elif tag == 'message':
                    self.status.set(event[1])
                    self.append_log(event[1])
                elif tag == 'preview':
                    from PIL import Image, ImageTk
                    with Image.open(event[1]) as img:
                        img.thumbnail((450, 280))
                        self.preview_image = ImageTk.PhotoImage(img.copy())
                    self.preview.configure(image=self.preview_image, text='')
        except queue.Empty:
            pass
        except Exception as exc:
            self.append_log('ERROR: ' + str(exc))
        self.after(100, self.poll)

    def close(self):
        if self.busy:
            self.cancel.set()
            self.status.set('Cancelling… waiting for the current request to finish.')
            self.after(150, self.close_when_idle)
        else:
            self.destroy()

    def close_when_idle(self):
        if self.busy:
            self.after(150, self.close_when_idle)
        else:
            self.destroy()

if __name__ == '__main__':
    Browser().mainloop()
