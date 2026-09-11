"""批量账号数值查询 - 移动端 (Kivy)，经 Buildozer 打包为 Android APK。

本文件**自包含**：配置模型 / 账号读取 / 查询引擎全部内联，不依赖其他 .py 模块，
方便在手机上一次性上传到 GitHub 做云构建。桌面版 app.py 仍使用模块化文件，互不影响。
"""
from __future__ import annotations

__version__ = "0.1"

import csv
import hashlib
import json
import os
import queue
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional

import requests
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.recycleview import RecycleView
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput

Window.orientation = "portrait"


# ===================== 配置模型 =====================
@dataclass
class QueryConfig:
    url_template: str = ""
    method: str = "GET"
    headers: str = "{}"
    body_template: str = "{}"
    value_path: str = "data.points"
    concurrency: int = 5
    retries: int = 1
    timeout: float = 10.0
    demo_mode: bool = False

    def resolve(self, account: str):
        """把 {account} 占位符替换掉，返回 (method, url, headers, json_body)。"""
        url = self.url_template.replace("{account}", account)
        raw_headers = self.headers.replace("{account}", account)
        raw_body = self.body_template.replace("{account}", account)
        return (
            self.method.upper(),
            url,
            _parse_json(raw_headers) or {},
            (_parse_json(raw_body) if self.method.upper() == "POST" else None),
        )


def _parse_json(text: str):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:  # noqa: BLE001
        raise ValueError(f"JSON 解析失败: {e}")


def extract_value(data, path: str):
    """按点号路径从嵌套字典里取值，例如 data.points。"""
    if not path:
        return None
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


# ===================== 账号读取 =====================
def parse_text_accounts(text: str) -> list[str]:
    if not text:
        return []
    items: list[str] = []
    for line in text.replace(",", "\n").splitlines():
        acc = line.strip().strip('"').strip("'")
        if acc:
            items.append(acc)
    return items


def read_accounts_csv(path: str, column=None) -> list[str]:
    """纯 Python 读 CSV（不依赖 pandas，便于在 Android 上运行）。"""
    with open(path, newline="", encoding="utf-8-sig") as fp:
        rows = list(csv.reader(fp))
    if not rows:
        return []
    header = rows[0]
    if column:
        if isinstance(column, str) and column in header:
            idx = header.index(column)
        else:
            idx = int(column) if str(column).isdigit() else 0
    else:
        idx = 0
    return [r[idx].strip() for r in rows[1:] if len(r) > idx and r[idx].strip()]


def merge_accounts(*lists) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for acc in lst or []:
            acc = str(acc).strip()
            if acc and acc not in seen:
                seen.add(acc)
                out.append(acc)
    return out


# ===================== 查询引擎 =====================
@dataclass
class Result:
    account: str
    value: Optional[object]
    status: str


def _demo_response(account: str):
    h = int(hashlib.md5(account.encode("utf-8")).hexdigest(), 16)
    return {"code": 0, "msg": "ok",
            "data": {"points": h % 100000, "level": h % 100, "name": account}}, None


def _http_request(config: QueryConfig, account: str, session: requests.Session):
    method, url, headers, json_body = config.resolve(account)
    if not url:
        return None, "URL 模板为空"
    try:
        resp = session.request(method, url, headers=headers, json=json_body,
                               timeout=config.timeout)
        resp.raise_for_status()
        return resp.json(), None
    except Exception as e:  # noqa: BLE001
        return None, f"请求异常: {e}"


def query_one(config: QueryConfig, account: str, session: requests.Session) -> Result:
    data, err = None, "未知错误"
    for _ in range(config.retries + 1):
        data, err = (_demo_response(account) if config.demo_mode
                     else _http_request(config, account, session))
        if err is None:
            break
    if err is not None:
        return Result(account, None, err)
    value = extract_value(data, config.value_path)
    if value is None:
        return Result(account, None, f"取值失败(路径 '{config.value_path}' 无匹配)")
    return Result(account, value, "成功")


def run_batch(config: QueryConfig, accounts: list[str],
              progress: Optional[Callable[[int, int, Result], None]] = None) -> list[Result]:
    order = {acc: i for i, acc in enumerate(accounts)}
    results: list[Result] = []
    with requests.Session() as session:
        with ThreadPoolExecutor(max_workers=max(1, config.concurrency)) as ex:
            futures = {ex.submit(query_one, config, acc, session): acc
                       for acc in accounts}
            done = 0
            for fut in as_completed(futures):
                res = fut.result()
                results.append(res)
                done += 1
                if progress:
                    progress(done, len(accounts), res)
    results.sort(key=lambda r: order.get(r.account, 0))
    return results


# ===================== Kivy UI =====================
class ResultsView(RecycleView):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.data = []


class AccountQueryApp(App):
    def build(self):
        self.accounts: list[str] = []
        self.file_accounts: list[str] = []
        self.running = False
        self.result_rows: list[tuple] = []
        self.result_queue: "queue.Queue" = queue.Queue()

        root = ScrollView()
        body = BoxLayout(orientation="vertical", size_hint_y=None, spacing=8, padding=10)
        body.bind(minimum_height=body.setter("height"))

        def section(title):
            body.add_widget(Label(text=title, size_hint_y=None, height=34,
                                  font_size=18, bold=True, color=(0.4, 0.8, 1, 1)))

        def field(label_text, text, height=34, multiline=False):
            row = BoxLayout(orientation="vertical", size_hint_y=None, height=height + 26)
            row.add_widget(Label(text=label_text, size_hint_y=None, height=22,
                                font_size=13, halign="left", color=(0.8, 0.8, 0.8, 1)))
            ti = TextInput(text=text, size_hint_y=None, height=height,
                           multiline=multiline, font_size=14)
            row.add_widget(ti)
            body.add_widget(row)
            return ti

        # ---- 接口配置 ----
        section("接口配置")
        self.url_in = field("URL 模板 (用 {account} 占位):",
                            "https://api.example.com/user/{account}")
        meth_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=40)
        meth_row.add_widget(Label(text="方法:", size_hint=(0.25, 1), font_size=14))
        self.method_spin = Spinner(text="GET", values=["GET", "POST"], size_hint=(0.35, 1))
        meth_row.add_widget(self.method_spin)
        meth_row.add_widget(Label(text="取值路径:", size_hint=(0.25, 1), font_size=14))
        self.value_in = TextInput(text="data.points", size_hint=(0.4, 1), font_size=14)
        meth_row.add_widget(self.value_in)
        body.add_widget(meth_row)
        self.headers_in = field("Headers (JSON):", "{}", height=60, multiline=True)
        self.body_in = field("Body (JSON, 可含 {account}):", "{}", height=60, multiline=True)
        opt_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=40)
        opt_row.add_widget(Label(text="并发数:", size_hint=(0.3, 1), font_size=14))
        self.conc_in = TextInput(text="5", size_hint=(0.2, 1), font_size=14)
        opt_row.add_widget(self.conc_in)
        opt_row.add_widget(Label(text="重试:", size_hint=(0.25, 1), font_size=14))
        self.retry_in = TextInput(text="1", size_hint=(0.2, 1), font_size=14)
        opt_row.add_widget(self.retry_in)
        body.add_widget(opt_row)
        demo_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=40)
        self.demo_chk = CheckBox(size_hint=(0.1, 1))
        demo_row.add_widget(self.demo_chk)
        demo_row.add_widget(Label(text="演示模式(内置假接口, 无需真实API)",
                                  size_hint=(0.9, 1), font_size=14))
        body.add_widget(demo_row)

        # ---- 账号输入 ----
        section("账号输入 (每行一个或逗号分隔, 也可选CSV文件)")
        self.accounts_in = TextInput(hint_text="在此粘贴账号...", size_hint_y=None,
                                     height=120, multiline=True, font_size=14)
        body.add_widget(self.accounts_in)
        file_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=44)
        self.pick_btn = Button(text="选择 CSV 文件", size_hint=(0.5, 1), font_size=14)
        self.pick_btn.bind(on_release=self._pick_file)
        file_row.add_widget(self.pick_btn)
        self.file_label = Label(text="未选择文件", size_hint=(0.5, 1), font_size=13,
                                color=(0.8, 0.8, 0.8, 1))
        file_row.add_widget(self.file_label)
        body.add_widget(file_row)

        # ---- 操作 ----
        act_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=48)
        self.start_btn = Button(text="开始查询", font_size=16, background_color=(0.2, 0.6, 1, 1))
        self.start_btn.bind(on_release=lambda *_: self._start())
        act_row.add_widget(self.start_btn)
        self.export_btn = Button(text="导出 CSV", font_size=15)
        self.export_btn.bind(on_release=lambda *_: self._export())
        act_row.add_widget(self.export_btn)
        body.add_widget(act_row)
        self.progress_label = Label(text="0 / 0", size_hint_y=None, height=26, font_size=13)
        body.add_widget(self.progress_label)

        # ---- 结果 ----
        section("查询结果")
        self.results = ResultsView(size_hint=(1, None), height=300)
        self.results.viewclass = "Label"
        self.results.add_widget(Label(text="账号  |  数值  |  状态", size_hint_y=None, height=28,
                                      font_size=13, bold=True, color=(0.7, 0.7, 0.7, 1)))
        body.add_widget(self.results)

        root.add_widget(body)
        return root

    # ---------- 文件选择 ----------
    def _pick_file(self, *_):
        mv = ModalView(size_hint=(0.95, 0.85))
        fc = FileChooserListView(filters=["*.csv"])
        fc.bind(on_selection=lambda inst, val: self._on_file(mv, val))
        mv.add_widget(fc)
        mv.open()

    def _on_file(self, mv, selection):
        if selection:
            path = selection[0]
            try:
                self.file_accounts = read_accounts_csv(path, column=0)
                self.file_label.text = f"{path.rsplit('/', 1)[-1]} ({len(self.file_accounts)} 个)"
            except Exception as e:  # noqa: BLE001
                self.file_label.text = f"读取失败: {e}"
        mv.dismiss()

    # ---------- 查询 ----------
    def _collect_accounts(self) -> list[str]:
        return merge_accounts(parse_text_accounts(self.accounts_in.text), self.file_accounts)

    def _build_config(self) -> QueryConfig:
        return QueryConfig(
            url_template=self.url_in.text,
            method=self.method_spin.text,
            headers=self.headers_in.text.strip() or "{}",
            body_template=self.body_in.text.strip() or "{}",
            value_path=self.value_in.text.strip(),
            concurrency=int(self.conc_in.text or 5),
            retries=int(self.retry_in.text or 0),
            timeout=10.0,
            demo_mode=self.demo_chk.active,
        )

    def _start(self, *_):
        if self.running:
            return
        self.accounts = self._collect_accounts()
        if not self.accounts:
            self.progress_label.text = "请先输入账号"
            return
        try:
            cfg = self._build_config()
        except ValueError as e:
            self.progress_label.text = f"配置错误: {e}"
            return
        self.running = True
        self.start_btn.disabled = True
        self.results.data = []
        self.result_rows = []
        self.progress_label.text = f"0 / {len(self.accounts)}"
        total = len(self.accounts)

        def worker():
            def progress(done, tot, res):
                self.result_queue.put((done, tot, res))

            try:
                run_batch(cfg, self.accounts, progress=progress)
            except Exception as e:  # noqa: BLE001
                self.result_queue.put(("ERROR", str(e)))
            finally:
                self.result_queue.put(("DONE", None))

        threading.Thread(target=worker, daemon=True).start()
        Clock.schedule_interval(lambda dt: self._poll(total), 0.05)

    def _poll(self, total):
        try:
            while True:
                item = self.result_queue.get_nowait()
                if item[0] == "DONE":
                    self._finish()
                    return False
                if item[0] == "ERROR":
                    self.progress_label.text = f"运行异常: {item[1]}"
                    self._finish()
                    return False
                done, _, res = item
                self.result_rows.append((res.account, res.value, res.status))
                self.results.data.append(
                    {"text": f"{res.account}  |  {res.value}  |  {res.status}"}
                )
                self.progress_label.text = f"{done} / {total}"
        except queue.Empty:
            pass
        return bool(self.running)

    def _finish(self):
        self.running = False
        self.start_btn.disabled = False
        self.progress_label.text += "  完成"

    def _export(self, *_):
        if not self.result_rows:
            self.progress_label.text = "没有结果可导出"
            return
        path = os.path.join(self.user_data_dir, "results.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as fp:
            fp.write("账号,数值,状态\n")
            for acc, val, st in self.result_rows:
                fp.write(f"{acc},{val},{st}\n")
        self.progress_label.text = f"已导出: {path}"


if __name__ == "__main__":
    AccountQueryApp().run()
