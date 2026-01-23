# -*- coding: utf-8 -*-
import os
import json
import random
import subprocess
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from threading import Thread
import traceback
import atexit

# --- 崩溃日志记录 ---
def log_crash(error_msg):
    with open("crash_log.txt", "w", encoding="utf-8") as f:
        f.write(error_msg)

# --- 配置文件路径 ---
CONFIG_FILE = "soupy_config.json"

def find_executable(name):
    """系统路径搜索"""
    path = shutil.which(name)
    if path: return path
    m_chip_path = f"/opt/homebrew/bin/{name}"
    if os.path.exists(m_chip_path): return m_chip_path
    intel_path = f"/usr/local/bin/{name}"
    if os.path.exists(intel_path): return intel_path
    return name

FFMPEG_PATH = find_executable("ffmpeg")
FFPROBE_PATH = find_executable("ffprobe")

class SoupyClipApp:
    def __init__(self, root):
        self.root = root
        self.root.title("SoupyClip")
        self.root.geometry("800x920")
        self.root.configure(bg="#FFFFFF")
        
        atexit.register(self.cleanup_temp)
        
        # --- 视觉系统 (Visual System) ---
        self.colors = {
            "bg": "#FFFFFF",           # 全局纯白
            "text_main": "#111111",    # 极致黑
            "text_sub": "#888888",     # 高级灰
            "accent": "#000000",       # 强调色（纯黑）
            "input_bg": "#F7F7F5",     # Notion 风格输入框底色
            "border": "#E0E0E0",       # 极细分割线
            "log_bg": "#F4F5F7",       # 舒适的工坊灰
            "log_text": "#374151"      # 深岩灰文字
        }
        
        self.fonts = {
            "display": ("PingFang SC", 32, "bold"),
            "h2": ("PingFang SC", 13, "bold"),
            "body": ("PingFang SC", 12),
            "small": ("PingFang SC", 11),
            "btn": ("PingFang SC", 16, "bold"),
            "log": ("Menlo", 11)
        }

        # 数据初始化
        self.paths = {"source_videos": "", "source_layers": "", "source_audios": "", "output_videos": ""}
        self.num_output = tk.StringVar(value="25")
        self.apply_tweak = tk.BooleanVar(value=True)
        self.recent_projects = []
        self.is_running = False
        
        self.load_settings()
        self.create_widgets()
        self.check_env()

    def cleanup_temp(self):
        """退出清理"""
        if self.paths["output_videos"]:
            temp_dir = os.path.join(self.paths["output_videos"], "_soupy_work_")
            if os.path.exists(temp_dir):
                try: shutil.rmtree(temp_dir)
                except: pass

    def check_env(self):
        try:
            subprocess.run([FFMPEG_PATH, "-version"], capture_output=True, check=True)
            self.log("✅ 环境依赖已安装")
        except:
            self.log("❌ 错误：未检测到 FFmpeg 核心组件")

    def create_widgets(self):
        # 全局容器
        main_pad = tk.Frame(self.root, bg=self.colors["bg"])
        main_pad.pack(fill="both", expand=True, padx=45, pady=40)

        # 1. Header (极致简约)
        header = tk.Frame(main_pad, bg=self.colors["bg"])
        header.pack(fill="x", pady=(0, 25))
        
        tk.Label(header, text="SoupyClip.", font=self.fonts["display"], 
                 bg=self.colors["bg"], fg=self.colors["text_main"]).pack(anchor="w")
        
        # 2. 智能导入与历史记录
        smart_frame = tk.LabelFrame(main_pad, text=" PROJECT IMPORT ", font=("Helvetica", 10, "bold"),
                                   bg=self.colors["bg"], fg=self.colors["text_sub"], 
                                   bd=1, relief="solid", padx=20, pady=20)
        smart_frame.pack(fill="x", pady=(0, 20))

        # 文件夹结构提示
        hint_frame = tk.Frame(smart_frame, bg=self.colors["bg"])
        hint_frame.pack(fill="x", pady=(0, 10))
        tk.Label(hint_frame, text="💡 智能识别：请选择包含视频、音频、贴图的产品主文件夹", 
                 font=self.fonts["small"], bg=self.colors["bg"], fg=self.colors["text_sub"]).pack(anchor="w")

        # 历史记录下拉框 + 按钮
        combo_row = tk.Frame(smart_frame, bg=self.colors["bg"])
        combo_row.pack(fill="x", pady=5)
        
        self.project_combo = ttk.Combobox(combo_row, values=self.recent_projects, font=self.fonts["body"], height=5)
        self.project_combo.pack(side="left", fill="x", expand=True, ipady=4)
        self.project_combo.set("选择或输入产品主目录...")
        self.project_combo.bind("<<ComboboxSelected>>", self.on_project_select)

        btn_browse = tk.Button(combo_row, text="浏览...", font=self.fonts["small"],
                              bg=self.colors["input_bg"], relief="flat", padx=15,
                              command=self.smart_import_browse)
        btn_browse.pack(side="right", padx=(10, 0))

        # 3. 路径详情 (自动填充区)
        path_group = tk.Frame(main_pad, bg=self.colors["bg"])
        path_group.pack(fill="x")

        self.path_vars = {}
        labels = [
            ("source_videos", "视频素材"), 
            ("source_layers", "顶层贴图"), 
            ("source_audios", "背景音频"), 
            ("output_videos", "导出位置")
        ]
        
        for key, text in labels:
            row = tk.Frame(path_group, bg=self.colors["bg"])
            row.pack(fill="x", pady=5)
            
            tk.Label(row, text=text, font=self.fonts["body"], bg=self.colors["bg"], 
                    fg=self.colors["text_main"], width=8, anchor="w").pack(side="left")
            
            var = tk.StringVar(value=self.paths.get(key, ""))
            self.path_vars[key] = var
            
            entry = tk.Entry(row, textvariable=var, font=self.fonts["small"], 
                            bg=self.colors["input_bg"], fg="#555", 
                            relief="flat", highlightthickness=0)
            entry.pack(side="left", fill="x", expand=True, ipady=6, padx=5)

        # 分割线
        tk.Frame(main_pad, height=1, bg=self.colors["border"]).pack(fill="x", pady=25)

        # 4. 参数控制
        ctrl_row = tk.Frame(main_pad, bg=self.colors["bg"])
        ctrl_row.pack(fill="x")

        tk.Label(ctrl_row, text="生成数量", font=self.fonts["h2"], bg=self.colors["bg"]).pack(side="left")
        entry_num = tk.Entry(ctrl_row, textvariable=self.num_output, width=5, font=("PingFang SC", 14),
                            bg=self.colors["input_bg"], relief="flat", justify="center")
        entry_num.pack(side="left", padx=10, ipady=3)
        
        tk.Checkbutton(ctrl_row, text="启用 AI 画面随机微调", 
                       variable=self.apply_tweak, font=self.fonts["body"], 
                       bg=self.colors["bg"], activebackground=self.colors["bg"], 
                       command=self.save_settings).pack(side="right")

        # 5. 日志区
        tk.Label(main_pad, text="TERMINAL OUTPUT", font=("Helvetica", 9, "bold"), 
                bg=self.colors["bg"], fg="#CCCCCC").pack(anchor="w", pady=(25, 5))
        
        self.log_text = tk.Text(main_pad, height=8, bg=self.colors["log_bg"], fg=self.colors["log_text"], 
                               font=self.fonts["log"], relief="flat", padx=15, pady=15, 
                               highlightthickness=0)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.config(state="disabled")

        # 6. 底部主按钮 (Sticky Footer)
        self.btn_start = tk.Button(self.root, text="🚀 开始执行批量混剪", font=self.fonts["btn"],
                                  fg="#FFFFFF", bg="#000000", activebackground="#333333", activeforeground="#FFFFFF",
                                  relief="flat", bd=0, cursor="hand2",
                                  command=self.start_task_thread)
        self.btn_start.place(relx=0, rely=1, anchor="sw", width=800, height=80) 

    # --- 核心逻辑 ---

    def smart_import_browse(self):
        parent_dir = filedialog.askdirectory()
        if not parent_dir: return
        self.process_smart_import(parent_dir)

    def on_project_select(self, event):
        selected_path = self.project_combo.get()
        if selected_path and os.path.exists(selected_path):
            self.process_smart_import(selected_path)

    def process_smart_import(self, parent_dir):
        # 更新历史记录
        if parent_dir in self.recent_projects:
            self.recent_projects.remove(parent_dir)
        self.recent_projects.insert(0, parent_dir)
        self.recent_projects = self.recent_projects[:5]
        self.project_combo['values'] = self.recent_projects
        self.project_combo.set(parent_dir)
        
        # 文件类型指纹
        signatures = {
            "source_videos": ('.mp4', '.mov', '.avi', '.mkv'),
            "source_layers": ('.png',),
            "source_audios": ('.aac', '.mp3', '.wav', '.m4a')
        }
        
        detected = {}
        try:
            subdirs = [os.path.join(parent_dir, d) for d in os.listdir(parent_dir) if os.path.isdir(os.path.join(parent_dir, d))]
        except: return

        # 深度扫描子目录内容
        for subdir in subdirs:
            counts = {"video": 0, "layer": 0, "audio": 0}
            try:
                files = os.listdir(subdir)
                if not files: continue
                for f in files:
                    if f.startswith('.'): continue
                    low = f.lower()
                    if low.endswith(signatures["source_videos"]): counts["video"] += 1
                    elif low.endswith(signatures["source_layers"]): counts["layer"] += 1
                    elif low.endswith(signatures["source_audios"]): counts["audio"] += 1
                
                if max(counts.values()) == 0: continue
                dom = max(counts, key=counts.get)
                
                if dom == "video" and "source_videos" not in detected: detected["source_videos"] = subdir
                elif dom == "layer" and "source_layers" not in detected: detected["source_layers"] = subdir
                elif dom == "audio" and "source_audios" not in detected: detected["source_audios"] = subdir
            except: continue

        # 自动创建输出目录
        if "output_videos" not in detected:
            auto = os.path.join(parent_dir, "Output")
            if not os.path.exists(auto):
                try: os.makedirs(auto)
                except: pass
            detected["output_videos"] = auto

        # 填入路径
        found_c = 0
        for k, p in detected.items():
            if k in self.path_vars:
                self.path_vars[k].set(p)
                self.paths[k] = p
                found_c += 1
        
        self.save_settings()
        
        if found_c >= 3:
            self.log(f"📁 已加载项目: {os.path.basename(parent_dir)} (识别到 {found_c} 个目录)")
        else:
            self.log("⚠️ 警告：仅识别到部分目录，请手动检查。")

    def log(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"> {msg}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def load_settings(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k in self.paths:
                        if k in data: self.paths[k] = data[k]
                    if "num_output" in data: self.num_output.set(data["num_output"])
                    if "apply_tweak" in data: self.apply_tweak.set(data["apply_tweak"])
                    if "recent_projects" in data: self.recent_projects = data["recent_projects"]
            except: pass

    def save_settings(self):
        data = {k: v.get() for k, v in self.path_vars.items()}
        data["num_output"] = self.num_output.get()
        data["apply_tweak"] = self.apply_tweak.get()
        data["recent_projects"] = self.recent_projects
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    def get_duration(self, path):
        cmd = [FFPROBE_PATH, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', path]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return float(res.stdout.strip())
        except: return None

    def start_task_thread(self):
        if self.is_running: return
        self.save_settings()
        for var in self.path_vars.values():
            if not var.get() or not os.path.exists(var.get()):
                messagebox.showwarning("设置不完整", "请先正确配置所有文件夹路径。")
                return
        self.is_running = True
        self.btn_start.config(state="disabled", text="⏳ 正在全力处理中，请勿关闭...", bg="#555555")
        Thread(target=self.run_task, daemon=True).start()

    def run_task(self):
        try:
            p = {k: v.get() for k, v in self.path_vars.items()}
            out_dir = p["output_videos"]
            temp_dir = os.path.join(out_dir, "_soupy_work_")
            target = int(self.num_output.get())
            use_tweak = self.apply_tweak.get()
            count = 0
            
            vids = [f for f in os.listdir(p["source_videos"]) if f.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')) and not f.startswith('.')]
            imgs = [f for f in os.listdir(p["source_layers"]) if f.lower().endswith('.png') and not f.startswith('.')]
            auds = [f for f in os.listdir(p["source_audios"]) if f.lower().endswith(('.aac', '.mp3', '.wav', '.m4a')) and not f.startswith('.')]

            while count < target:
                if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
                os.makedirs(temp_dir)
                
                # --- v12.0 性能升级：单次编码 + 预选素材 ---
                current_img_path = os.path.join(p["source_layers"], random.choice(imgs)) if imgs else None
                current_audio_path = os.path.join(p["source_audios"], random.choice(auds)) if auds else None
                
                dur = random.choice([17, 18, 19, 20])
                n = random.randint(6, 10)
                self.log(f"🎬 正在制作第 {count+1}/{target} 条视频 [目标时长: {dur}s]")
                
                parts = []
                rem = dur - n
                cuts = sorted([random.uniform(0, rem) for _ in range(n-1)])
                last = 0
                for c in cuts: parts.append(1.0 + (c - last)); last = c
                parts.append(1.0 + (rem - last))
                random.shuffle(parts)

                ok = True
                selected = []
                pool = list(vids)
                for d_need in parts:
                    match = None
                    random.shuffle(pool)
                    for c in pool:
                        cp = os.path.join(p["source_videos"], c)
                        actual = self.get_duration(cp)
                        if actual and actual >= d_need:
                            match = (cp, d_need, actual); pool.remove(c); break
                    if not match: ok = False; break
                    selected.append(match)
                
                if not ok: self.log("⚠️ 警告：素材不足匹配当前随机方案，正在重试..."); continue

                list_txt = os.path.join(temp_dir, "l.txt")
                with open(list_txt, "w", encoding='utf-8') as f:
                    for idx, (path, d, full) in enumerate(selected):
                        out = os.path.join(temp_dir, f"{idx}.mp4")
                        ss = random.uniform(0, full - d)
                        
                        filter_chain = ""
                        filter_chain += "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1"
                        
                        if use_tweak:
                            br, co, sa, sh = random.uniform(-0.02, 0.02), random.uniform(0.98, 1.02), random.uniform(0.98, 1.02), random.uniform(1.0, 1.05)
                            filter_chain += f",eq=brightness={br}:contrast={co}:saturation={sa},unsharp=luma_msize_x=3:luma_msize_y=3:luma_amount={sh}"
                        
                        inputs_cmd = ['-ss', f'{ss:.4f}', '-i', path]
                        if current_img_path:
                            inputs_cmd.extend(['-i', current_img_path])
                            filter_chain += f"[bg];[bg][1:v]overlay=0:0"
                        
                        cmd = [FFMPEG_PATH, '-hwaccel', 'videotoolbox'] + inputs_cmd + [
                            '-t', f'{d:.4f}', 
                            '-filter_complex' if current_img_path else '-vf', filter_chain, 
                            '-c:v', 'h264_videotoolbox', '-b:v', '6M', '-an', '-y', out
                        ]
                        
                        subprocess.run(cmd, capture_output=True, check=True)
                        f.write(f"file '{idx}.mp4'\n")

                merged = os.path.join(temp_dir, "m.mp4")
                subprocess.run([FFMPEG_PATH, '-f', 'concat', '-safe', '0', '-i', list_txt, '-c', 'copy', '-y', merged], check=True, capture_output=True)
                
                final_name = f"Soupy_Export_{count+1}.mp4"
                final = os.path.join(out_dir, final_name)
                
                final_cmd = [FFMPEG_PATH, '-i', merged]
                if current_audio_path:
                    final_cmd.extend(['-i', current_audio_path, '-map', '0:v', '-map', '1:a'])
                else:
                    final_cmd.extend(['-map', '0:v'])
                
                final_cmd.extend(['-c:v', 'copy', '-c:a', 'aac', '-shortest', '-y', final])
                
                subprocess.run(final_cmd, check=True, capture_output=True)
                
                count += 1
                self.log(f"✅ 完成：{final_name}")

            messagebox.showinfo("Success", f"🎉 任务圆满完成！\n共生成 {target} 条视频。")
            
            # --- 自动打开输出文件夹 ---
            try:
                subprocess.run(["open", out_dir])
            except: pass
            
        except Exception as e:
            self.log(f"❌ 运行错误: {e}")
            messagebox.showerror("Error", str(e))
        finally:
            self.is_running = False
            self.btn_start.config(state="normal", text="🚀 开始执行批量混剪", bg="#000000")
            self.cleanup_temp()

if __name__ == "__main__":
    try:
        root = tk.Tk()
        app = SoupyClipApp(root)
        root.mainloop()
    except Exception:
        log_crash(traceback.format_exc())