"""MK11 MKSwap Mod Merger - window app around mk11_merge.py."""
import os, queue, sys, threading, traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mk11_merge as mm

HOW_TO = (
    "Use this when two MKSwap mods replace the same package (same file name, e.g. "
    "GEARASSETS_SCO_ScriptAssets) and Windows asks you to overwrite.\n\n"
    "1.  Game folder: your Mortal Kombat 11 install. The ORIGINAL files in its Asset folder must be untouched.\n"
    "2.  Mod 1 / Mod 2: the folders holding each mod's .xxx and .psf. Picking the folder one level above also works.\n"
    "     A mod that is already installed can be picked straight from MKSwap\\Asset.\n"
    "3.  Output folder: where the merged .xxx and .psf are written.\n"
    "4.  Tick \"Install\" to copy the result into MKSwap\\Asset for you. The files already there are moved to a\n"
    "     MKSwap_backup_<date> folder in the game folder first.\n"
    "5.  Click MERGE. It takes a minute or two (the .psf is over 1 GB).\n\n"
    "If two mods edit the same asset (for example the same skin), the merge stops and tells you instead of breaking the game."
)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('MK11 MKSwap Mod Merger')
        self.minsize(760, 640)
        self.q = queue.Queue()
        self.busy = False

        pad = {'padx': 10, 'pady': 4}
        style = ttk.Style(self)
        style.configure('Big.TButton', font=('Segoe UI', 13, 'bold'), padding=10)
        style.configure('Head.TLabel', font=('Segoe UI', 15, 'bold'))

        ttk.Label(self, text='MK11 MKSwap Mod Merger', style='Head.TLabel').pack(anchor='w', padx=10, pady=(10, 0))

        how = ttk.LabelFrame(self, text='How to use')
        how.pack(fill='x', **pad)
        ttk.Label(how, text=HOW_TO, justify='left', wraplength=720).pack(anchor='w', padx=8, pady=6)

        form = ttk.Frame(self)
        form.pack(fill='x', **pad)
        form.columnconfigure(1, weight=1)
        self.game = tk.StringVar(value=mm.DEFAULT_GAME)
        self.mod1 = tk.StringVar(value=os.path.join(mm.DEFAULT_GAME, 'MKSwap', 'Asset') if mm.DEFAULT_GAME else '')
        self.mod2 = tk.StringVar()
        self.out = tk.StringVar(value=os.path.join(HERE, 'merged'))
        self.install = tk.BooleanVar(value=True)
        self.extra = []
        self.form = form
        self._row(0, 'Game folder', self.game)
        self._row(1, 'Mod 1 folder', self.mod1)
        self._row(2, 'Mod 2 folder', self.mod2)
        self.next_row = 3
        self.add_btn = ttk.Button(form, text='+ Add another mod', command=self.add_mod)
        self.add_btn.grid(row=99, column=1, sticky='w', pady=2)
        self._row(100, 'Output folder', self.out)
        ttk.Checkbutton(form, text='Install into MKSwap\\Asset after merging (backs up the current files first)',
                        variable=self.install).grid(row=101, column=1, sticky='w', pady=4)

        self.btn = ttk.Button(self, text='MERGE', style='Big.TButton', command=self.start)
        self.btn.pack(fill='x', padx=10, pady=6)
        self.bar = ttk.Progressbar(self, mode='indeterminate')
        self.bar.pack(fill='x', padx=10)
        self.status = tk.StringVar(value='Ready.')
        ttk.Label(self, textvariable=self.status).pack(anchor='w', padx=10, pady=(4, 0))

        logf = ttk.Frame(self)
        logf.pack(fill='both', expand=True, padx=10, pady=(4, 10))
        self.log = tk.Text(logf, height=12, wrap='word', font=('Consolas', 9), state='disabled')
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        self.after(100, self.poll)

    def _row(self, r, label, var):
        ttk.Label(self.form, text=label).grid(row=r, column=0, sticky='w', pady=2)
        ttk.Entry(self.form, textvariable=var).grid(row=r, column=1, sticky='ew', padx=6, pady=2)
        ttk.Button(self.form, text='Browse...', command=lambda: self.browse(var, label)).grid(row=r, column=2, pady=2)

    def add_mod(self):
        var = tk.StringVar()
        self.extra.append(var)
        self._row(self.next_row, f'Mod {len(self.extra) + 2} folder', var)
        self.next_row += 1

    def browse(self, var, label):
        start = var.get() if os.path.isdir(var.get()) else os.path.dirname(var.get())
        d = filedialog.askdirectory(title=f'Choose {label}', initialdir=start or None, parent=self)
        if d:
            var.set(os.path.normpath(d))

    def write(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def start(self):
        if self.busy:
            return
        mods = [v.get().strip() for v in [self.mod1, self.mod2] + self.extra if v.get().strip()]
        if len(mods) < 2:
            messagebox.showwarning('MK11 Mod Merger', 'Pick at least two mod folders.', parent=self)
            return
        game, out = self.game.get().strip(), self.out.get().strip()
        if not out:
            messagebox.showwarning('MK11 Mod Merger', 'Pick an output folder.', parent=self)
            return
        self.busy = True
        self.btn.state(['disabled'])
        self.bar.start(12)
        self.status.set('Merging... (this takes a minute or two)')
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')
        threading.Thread(target=self.work, args=(mods, out, game, self.install.get()), daemon=True).start()

    def work(self, mods, out, game, install):
        log = lambda s: self.q.put(('log', s))
        try:
            name = mm.merge(mods, out, game, log)
            if install:
                log('')
                mm.install(os.path.abspath(out), name, game, log)
            self.q.put(('done', 'Installed! Start the game to try it.' if install else
                        'Done! Copy the two files from the output folder into MKSwap\\Asset.'))
        except mm.MergeError as e:
            self.q.put(('fail', str(e)))
        except Exception as e:
            log(traceback.format_exc())
            self.q.put(('fail', f'Unexpected error: {e}'))

    def poll(self):
        try:
            while True:
                kind, msg = self.q.get_nowait()
                if kind == 'log':
                    self.write(msg)
                    continue
                self.busy = False
                self.btn.state(['!disabled'])
                self.bar.stop()
                self.status.set(msg.splitlines()[0])
                self.write('\n' + msg)
                if kind == 'done':
                    messagebox.showinfo('MK11 Mod Merger', msg, parent=self)
                else:
                    messagebox.showerror('MK11 Mod Merger', msg, parent=self)
        except queue.Empty:
            pass
        self.after(100, self.poll)


if __name__ == '__main__':
    App().mainloop()
