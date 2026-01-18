/**
 * linkify_paths.js
 * チャット内のテキストからファイルパスを検出し、リンク化する
 */

(function() {
  // ファイルパスを検出するための正規表現
  // 1. 絶対パス (/) または 相対パス (./, ../) で始まるもの
  // 2. 拡張子を持つもの、またはディレクトリ構造を持つもの
  const pathRegex = /(?:\s|^)((?:\/|\.\.?\/)[a-zA-Z0-9\._\-\/]+(?:\.[a-zA-Z0-9]+)?)(?=\s|$|[.,!?;:])/g;

  /**
   * テキストノード内のパスをリンクに置換する
   */
  function linkifyElement(element) {
    if (element.hasAttribute('data-linkified')) return;
    
    // コードブロック内は除外
    if (element.closest('pre') || element.closest('code')) return;

    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, null, false);
    const nodesToReplace = [];

    let currentNode;
    while (currentNode = walker.nextNode()) {
      if (pathRegex.test(currentNode.nodeValue)) {
        nodesToReplace.push(currentNode);
      }
      pathRegex.lastIndex = 0; // reset regex
    }

    nodesToReplace.forEach(node => {
      const fragment = document.createDocumentFragment();
      let lastIndex = 0;
      let match;

      while ((match = pathRegex.exec(node.nodeValue)) !== null) {
        // マッチ前のテキストを追加
        fragment.appendChild(document.createTextNode(node.nodeValue.slice(lastIndex, match.index)));
        
        // スペースが含まれる場合を考慮
        const fullMatch = match[0];
        const path = match[1];
        const leadingSpace = fullMatch.startsWith(' ') ? ' ' : '';
        
        if (leadingSpace) {
          fragment.appendChild(document.createTextNode(leadingSpace));
        }

        // リンク要素を作成
        const link = document.createElement('span');
        link.className = 'file-path-link';
        link.textContent = path;
        link.title = `Open path: ${path}`;
        link.dataset.path = path;
        
        link.addEventListener('click', (e) => {
          e.preventDefault();
          console.log(`File path clicked: ${path}`);
          // ここにファイルを開くためのカスタムイベントなどを実装可能
          document.dispatchEvent(new CustomEvent('file-path-clicked', { detail: { path } }));
        });

        fragment.appendChild(link);
        lastIndex = pathRegex.lastIndex;
      }

      // 残りのテキストを追加
      fragment.appendChild(document.createTextNode(node.nodeValue.slice(lastIndex)));
      node.parentNode.replaceChild(fragment, node);
    });

    element.setAttribute('data-linkified', 'true');
  }

  /**
   * チャットコンテナの変更を監視
   */
  function observeChat(containerSelector) {
    const container = document.querySelector(containerSelector);
    if (!container) return;

    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        mutation.addedNodes.forEach((node) => {
          if (node.nodeType === Node.ELEMENT_NODE) {
            // メッセージ要素、またはその子要素をリンク化
            linkifyElement(node);
            node.querySelectorAll('*').forEach(linkifyElement);
          }
        });
      });
    });

    observer.observe(container, { childList: true, subtree: true });
    
    // 初回実行
    container.querySelectorAll('*').forEach(linkifyElement);
  }

  // DOMContentLoaded で初期化（または必要に応じてエクスポート）
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => observeChat('.chat-container'));
  } else {
    observeChat('.chat-container');
  }

  // グローバルに公開（外部からの呼び出し用）
  window.LinkifyPaths = {
    process: linkifyElement,
    observe: observeChat
  };
})();
