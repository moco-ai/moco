# -*- coding: utf-8 -*-
"""
agent-browser CLIラッパーツール

AI エージェントがブラウザを操作するためのツール群。
vercel-labs/agent-browser (https://github.com/vercel-labs/agent-browser) をベースに実装。

使用例:
    # 1. ページを開く
    browser_open("https://example.com")
    
    # 2. スナップショットを取得（インタラクティブ要素のみ）
    browser_snapshot(interactive=True)
    # 出力例:
    # - heading "Example Domain" [ref=e1] [level=1]
    # - button "Submit" [ref=e2]
    # - textbox "Email" [ref=e3]
    
    # 3. 要素を操作（ref を使用）
    browser_click("@e2")
    browser_fill("@e3", "test@example.com")
    
    # 4. テキストを取得
    browser_get_text("@e1")
"""
import subprocess
import shutil
import os
from typing import Optional, List


def _find_node_bin_dir() -> Optional[str]:
    """
    node/npx の bin ディレクトリを検出する。
    nvm 環境でも動作するように複数のパスを探索する。
    
    Returns:
        bin ディレクトリのパス（npx, node が含まれる）
    """
    # 1. まず PATH から npx を探す
    npx_path = shutil.which("npx")
    if npx_path:
        return os.path.dirname(npx_path)
    
    # 2. nvm のデフォルトパスを探索
    home = os.path.expanduser("~")
    nvm_base = os.path.join(home, ".nvm", "versions", "node")
    
    if os.path.isdir(nvm_base):
        # 最新バージョンを探す（バージョン番号でソート）
        versions = sorted(os.listdir(nvm_base), reverse=True)
        for version in versions:
            bin_dir = os.path.join(nvm_base, version, "bin")
            npx_candidate = os.path.join(bin_dir, "npx")
            if os.path.isfile(npx_candidate) and os.access(npx_candidate, os.X_OK):
                return bin_dir
    
    # 3. Homebrew などの一般的なパス
    common_paths = [
        "/usr/local/bin",
        "/opt/homebrew/bin",
    ]
    for path in common_paths:
        npx_candidate = os.path.join(path, "npx")
        if os.path.isfile(npx_candidate) and os.access(npx_candidate, os.X_OK):
            return path
    
    return None


def _run_agent_browser(*args: str, timeout: int = 60) -> str:
    """
    agent-browser CLIを実行するヘルパー関数
    
    Args:
        *args: コマンド引数
        timeout: タイムアウト秒数
        
    Returns:
        コマンドの出力
    """
    # 1. ワークスペース内のローカルインストールを優先
    # このファイルは moco-agent/src/moco/tools/ にあると仮定
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    local_bin = os.path.join(base_dir, "node_modules", ".bin", "agent-browser")
    
    if os.path.isfile(local_bin) and os.access(local_bin, os.X_OK):
        cmd = [local_bin] + list(args)
        # local_bin のディレクトリを PATH に追加（依存する node などが見つかるように）
        env = os.environ.copy()
        node_bin_dir = _find_node_bin_dir()
        if node_bin_dir:
            env["PATH"] = f"{node_bin_dir}:{env.get('PATH', '')}"
    else:
        # 2. node/npx の bin ディレクトリを検出して npx 経由で実行
        node_bin_dir = _find_node_bin_dir()
        if not node_bin_dir:
            return "Error: npx not found. Please install Node.js and npm, or check your nvm setup."
        
        npx_path = os.path.join(node_bin_dir, "npx")
        cmd = [npx_path, "agent-browser"] + list(args)
        
        # 環境変数を設定（node が見つかるように PATH を追加）
        env = os.environ.copy()
        current_path = env.get("PATH", "")
        env["PATH"] = f"{node_bin_dir}:{current_path}"
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env
        )
        
        output = result.stdout
        if result.stderr:
            # stderrがある場合は追加（エラーでない情報も含まれる場合がある）
            if result.returncode != 0:
                output += f"\nError: {result.stderr}"
        
        return output.strip() if output else "Command completed successfully."
        
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout}s"
    except FileNotFoundError:
        return "Error: agent-browser not found. Run 'npx agent-browser' once to install it."
    except Exception as e:
        return f"Error: {str(e)}"


def browser_open(url: str, headed: bool = False) -> str:
    """
    指定されたURLをブラウザで開きます。
    
    Args:
        url: 開くURL
        headed: Trueの場合、ブラウザウィンドウを表示（デバッグ用）
        
    Returns:
        結果メッセージ
        
    Example:
        browser_open("https://example.com")
        browser_open("https://example.com", headed=True)  # ブラウザ表示
    """
    args = ["open", url]
    if headed:
        args.append("--headed")
    return _run_agent_browser(*args)


def browser_snapshot(
    interactive: bool = True,
    compact: bool = False,
    depth: Optional[int] = None,
    selector: Optional[str] = None
) -> str:
    """
    ページのアクセシビリティスナップショットを取得します。
    スナップショットには各要素の ref（例: @e1）が含まれ、
    これを使って click, fill 等の操作を行います。
    
    Args:
        interactive: Trueの場合、インタラクティブ要素のみ（ボタン、入力、リンク等）
        compact: Trueの場合、空の構造要素を除去
        depth: ツリーの深さ制限
        selector: 特定のCSSセレクタにスコープを絞る
        
    Returns:
        アクセシビリティツリー（ref付き）
        
    Example:
        # インタラクティブ要素のみ取得
        snapshot = browser_snapshot(interactive=True)
        # 出力例:
        # - heading "Example Domain" [ref=e1]
        # - button "Submit" [ref=e2]
        # - textbox "Email" [ref=e3]
        
        # その後 ref を使って操作
        browser_click("@e2")
    """
    args = ["snapshot"]
    if interactive:
        args.append("-i")
    if compact:
        args.append("-c")
    if depth is not None:
        args.extend(["-d", str(depth)])
    if selector:
        args.extend(["-s", selector])
    return _run_agent_browser(*args, timeout=30)


def browser_click(ref_or_selector: str, double_click: bool = False) -> str:
    """
    要素をクリックします。
    
    Args:
        ref_or_selector: ref（@e1形式）またはCSSセレクタ
        double_click: Trueの場合、ダブルクリック
        
    Returns:
        結果メッセージ
        
    Example:
        browser_click("@e2")  # snapshot で取得した ref を使用
        browser_click("#submit-button")  # CSSセレクタ
        browser_click("@e3", double_click=True)
    """
    cmd = "dblclick" if double_click else "click"
    return _run_agent_browser(cmd, ref_or_selector)


def browser_fill(ref_or_selector: str, text: str) -> str:
    """
    入力フィールドをクリアしてテキストを入力します。
    
    Args:
        ref_or_selector: ref（@e1形式）またはCSSセレクタ
        text: 入力するテキスト
        
    Returns:
        結果メッセージ
        
    Example:
        browser_fill("@e3", "test@example.com")
        browser_fill("#email-input", "user@domain.com")
    """
    return _run_agent_browser("fill", ref_or_selector, text)


def browser_type(ref_or_selector: str, text: str) -> str:
    """
    要素にテキストをタイプします（既存テキストを保持）。
    
    Args:
        ref_or_selector: ref またはCSSセレクタ
        text: タイプするテキスト
        
    Returns:
        結果メッセージ
        
    Example:
        browser_type("@e3", "追加テキスト")
    """
    return _run_agent_browser("type", ref_or_selector, text)


def browser_press(key: str) -> str:
    """
    キーを押します。
    
    Args:
        key: キー名（Enter, Tab, Escape, Control+a 等）
        
    Returns:
        結果メッセージ
        
    Example:
        browser_press("Enter")
        browser_press("Control+a")
        browser_press("Tab")
    """
    return _run_agent_browser("press", key)


def browser_hover(ref_or_selector: str) -> str:
    """
    要素にマウスホバーします。
    
    Args:
        ref_or_selector: ref またはCSSセレクタ
        
    Returns:
        結果メッセージ
    """
    return _run_agent_browser("hover", ref_or_selector)


def browser_select(ref_or_selector: str, value: str) -> str:
    """
    ドロップダウンからオプションを選択します。
    
    Args:
        ref_or_selector: ref またはCSSセレクタ
        value: 選択する値
        
    Returns:
        結果メッセージ
    """
    return _run_agent_browser("select", ref_or_selector, value)


def browser_get_text(ref_or_selector: str) -> str:
    """
    要素のテキストコンテンツを取得します。
    
    Args:
        ref_or_selector: ref またはCSSセレクタ
        
    Returns:
        要素のテキスト
        
    Example:
        text = browser_get_text("@e1")
        text = browser_get_text("h1")
    """
    return _run_agent_browser("get", "text", ref_or_selector)


def browser_get_value(ref_or_selector: str) -> str:
    """
    入力要素の値を取得します。
    
    Args:
        ref_or_selector: ref またはCSSセレクタ
        
    Returns:
        入力値
    """
    return _run_agent_browser("get", "value", ref_or_selector)


def browser_get_url() -> str:
    """
    現在のページURLを取得します。
    
    Returns:
        現在のURL
    """
    return _run_agent_browser("get", "url")


def browser_get_title() -> str:
    """
    現在のページタイトルを取得します。
    
    Returns:
        ページタイトル
    """
    return _run_agent_browser("get", "title")


def browser_is_visible(ref_or_selector: str) -> str:
    """
    要素が表示されているかチェックします。
    
    Args:
        ref_or_selector: ref またはCSSセレクタ
        
    Returns:
        "true" または "false"
    """
    return _run_agent_browser("is", "visible", ref_or_selector)


def browser_is_enabled(ref_or_selector: str) -> str:
    """
    要素が有効（enabled）かチェックします。
    
    Args:
        ref_or_selector: ref またはCSSセレクタ
        
    Returns:
        "true" または "false"
    """
    return _run_agent_browser("is", "enabled", ref_or_selector)


def browser_wait(selector_or_ms: str) -> str:
    """
    要素が表示されるか、指定ミリ秒待機します。
    
    Args:
        selector_or_ms: CSSセレクタまたはミリ秒（例: "1000"）
        
    Returns:
        結果メッセージ
        
    Example:
        browser_wait("#loading-complete")  # 要素を待つ
        browser_wait("2000")  # 2秒待つ
    """
    return _run_agent_browser("wait", selector_or_ms)


def browser_screenshot(path: Optional[str] = None, full_page: bool = False) -> str:
    """
    スクリーンショットを取得します。
    
    Args:
        path: 保存先パス（省略時は自動生成）
        full_page: Trueの場合、ページ全体をキャプチャ
        
    Returns:
        保存されたファイルパスまたは結果メッセージ
        
    Example:
        browser_screenshot()  # 現在のビューポート
        browser_screenshot("/tmp/page.png", full_page=True)
    """
    args = ["screenshot"]
    if path:
        args.append(path)
    if full_page:
        args.append("--full")
    return _run_agent_browser(*args)


def browser_scroll(direction: str = "down", pixels: int = 500) -> str:
    """
    ページをスクロールします。
    
    Args:
        direction: スクロール方向（up, down, left, right）
        pixels: スクロール量（ピクセル）
        
    Returns:
        結果メッセージ
    """
    return _run_agent_browser("scroll", direction, str(pixels))


def browser_back() -> str:
    """
    前のページに戻ります。
    
    Returns:
        結果メッセージ
    """
    return _run_agent_browser("back")


def browser_forward() -> str:
    """
    次のページに進みます。
    
    Returns:
        結果メッセージ
    """
    return _run_agent_browser("forward")


def browser_reload() -> str:
    """
    ページをリロードします。
    
    Returns:
        結果メッセージ
    """
    return _run_agent_browser("reload")


def browser_eval(javascript: str) -> str:
    """
    ページでJavaScriptを実行します。
    
    Args:
        javascript: 実行するJavaScriptコード
        
    Returns:
        実行結果
        
    Example:
        browser_eval("document.title")
        browser_eval("window.scrollTo(0, 0)")
    """
    return _run_agent_browser("eval", javascript)


def browser_close() -> str:
    """
    ブラウザを閉じます。
    
    Returns:
        結果メッセージ
    """
    return _run_agent_browser("close")


def browser_console() -> str:
    """
    ブラウザコンソールのログを取得します。
    
    Returns:
        コンソールログ
    """
    return _run_agent_browser("console")


def browser_errors() -> str:
    """
    ページエラーを取得します。
    
    Returns:
        エラーログ
    """
    return _run_agent_browser("errors")


def browser_tab(action: str = "list", index: Optional[int] = None, url: Optional[str] = None) -> str:
    """
    タブを管理します。
    
    Args:
        action: アクション（list, new, close, または数値でタブ切替）
        index: タブインデックス（close, 切替時に使用）
        url: 新規タブで開くURL（new時に使用）
        
    Returns:
        結果メッセージ
        
    Example:
        browser_tab("list")  # タブ一覧
        browser_tab("new", url="https://example.com")  # 新規タブ
        browser_tab("close", index=1)  # タブを閉じる
    """
    args = ["tab"]
    if action == "list":
        pass  # デフォルト
    elif action == "new":
        args.append("new")
        if url:
            args.append(url)
    elif action == "close":
        args.append("close")
        if index is not None:
            args.append(str(index))
    else:
        # 数値の場合はタブ切替
        args.append(action)
    return _run_agent_browser(*args)


def browser_set_viewport(width: int, height: int) -> str:
    """
    ビューポートサイズを設定します。
    
    Args:
        width: 幅（ピクセル）
        height: 高さ（ピクセル）
        
    Returns:
        結果メッセージ
    """
    return _run_agent_browser("set", "viewport", str(width), str(height))


def browser_set_device(device_name: str) -> str:
    """
    デバイスをエミュレートします。
    
    Args:
        device_name: デバイス名（例: "iPhone 14", "iPad Pro"）
        
    Returns:
        結果メッセージ
    """
    return _run_agent_browser("set", "device", device_name)


