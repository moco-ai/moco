/**
 * Modal Component
 * 汎用的なモーダルダイアログの実装
 */

class Modal {
    constructor(options = {}) {
        this.options = {
            closeOnEscape: true,
            closeOnOverlayClick: true,
            animationDuration: 300,
            onOpen: null,
            onClose: null,
            ...options
        };
        
        this.isOpen = false;
        this.modalElement = null;
        this.overlayElement = null;
        
        this._createModal();
        this._setupEventListeners();
    }
    
    /**
     * モーダルのDOM要素を作成
     */
    _createModal() {
        // オーバーレイ
        this.overlayElement = document.createElement('div');
        this.overlayElement.className = 'modal-overlay';
        this.overlayElement.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0, 0, 0, 0.5);
            z-index: 9999;
            display: none;
            opacity: 0;
            transition: opacity ${this.options.animationDuration}ms ease;
        `;
        
        // モーダルコンテナ
        this.modalElement = document.createElement('div');
        this.modalElement.className = 'modal-container';
        this.modalElement.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%) scale(0.9);
            background-color: white;
            border-radius: 8px;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
            z-index: 10000;
            max-width: 90%;
            max-height: 90%;
            display: none;
            opacity: 0;
            transition: all ${this.options.animationDuration}ms ease;
        `;
        
        // モーダルヘッダー
        const header = document.createElement('div');
        header.className = 'modal-header';
        header.style.cssText = `
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px 24px;
            border-bottom: 1px solid #e0e0e0;
        `;
        
        // タイトル
        this.titleElement = document.createElement('h2');
        this.titleElement.className = 'modal-title';
        this.titleElement.style.cssText = `
            margin: 0;
            font-size: 20px;
            font-weight: 600;
            color: #333;
        `;
        
        // 閉じるボタン
        const closeButton = document.createElement('button');
        closeButton.className = 'modal-close';
        closeButton.innerHTML = '&times;';
        closeButton.style.cssText = `
            background: none;
            border: none;
            font-size: 28px;
            cursor: pointer;
            color: #999;
            padding: 0;
            width: 32px;
            height: 32px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 4px;
            transition: all 0.2s ease;
        `;
        closeButton.onmouseover = () => {
            closeButton.style.backgroundColor = '#f0f0f0';
            closeButton.style.color = '#333';
        };
        closeButton.onmouseout = () => {
            closeButton.style.backgroundColor = 'transparent';
            closeButton.style.color = '#999';
        };
        
        // モーダルコンテンツ
        this.contentElement = document.createElement('div');
        this.contentElement.className = 'modal-content';
        this.contentElement.style.cssText = `
            padding: 24px;
            overflow-y: auto;
            max-height: calc(90vh - 120px);
        `;
        
        // 要素の組み立て
        header.appendChild(this.titleElement);
        header.appendChild(closeButton);
        this.modalElement.appendChild(header);
        this.modalElement.appendChild(this.contentElement);
        
        // DOMに追加
        document.body.appendChild(this.overlayElement);
        document.body.appendChild(this.modalElement);
        
        // 閉じるボタンのイベント
        closeButton.addEventListener('click', () => this.close());
    }
    
    /**
     * イベントリスナーの設定
     */
    _setupEventListeners() {
        // オーバーレイクリック
        if (this.options.closeOnOverlayClick) {
            this.overlayElement.addEventListener('click', () => this.close());
        }
        
        // ESCキー
        if (this.options.closeOnEscape) {
            this._escapeHandler = (e) => {
                if (e.key === 'Escape' && this.isOpen) {
                    this.close();
                }
            };
            document.addEventListener('keydown', this._escapeHandler);
        }
    }
    
    /**
     * モーダルを開く
     * @param {Object} options - { title, content, onClose }
     */
    open(options = {}) {
        if (this.isOpen) return;
        
        // タイトル設定
        if (options.title) {
            this.setTitle(options.title);
        }
        
        // コンテンツ設定
        if (options.content) {
            this.setContent(options.content);
        }
        
        // 一時的なonCloseコールバック
        this._tempOnClose = options.onClose;
        
        // 表示
        this.overlayElement.style.display = 'block';
        this.modalElement.style.display = 'block';
        
        // アニメーション
        requestAnimationFrame(() => {
            this.overlayElement.style.opacity = '1';
            this.modalElement.style.opacity = '1';
            this.modalElement.style.transform = 'translate(-50%, -50%) scale(1)';
        });
        
        this.isOpen = true;
        
        // スクロール無効化
        document.body.style.overflow = 'hidden';
        
        // onOpenコールバック
        if (this.options.onOpen) {
            this.options.onOpen();
        }
    }
    
    /**
     * モーダルを閉じる
     */
    close() {
        if (!this.isOpen) return;
        
        // アニメーション
        this.overlayElement.style.opacity = '0';
        this.modalElement.style.opacity = '0';
        this.modalElement.style.transform = 'translate(-50%, -50%) scale(0.9)';
        
        // アニメーション完了後に非表示
        setTimeout(() => {
            this.overlayElement.style.display = 'none';
            this.modalElement.style.display = 'none';
            
            // スクロール有効化
            document.body.style.overflow = '';
            
            this.isOpen = false;
            
            // onCloseコールバック
            if (this._tempOnClose) {
                this._tempOnClose();
                this._tempOnClose = null;
            }
            if (this.options.onClose) {
                this.options.onClose();
            }
        }, this.options.animationDuration);
    }
    
    /**
     * タイトルを設定
     * @param {string} title
     */
    setTitle(title) {
        this.titleElement.textContent = title;
    }
    
    /**
     * コンテンツを設定
     * @param {string|HTMLElement} content
     */
    setContent(content) {
        if (typeof content === 'string') {
            this.contentElement.innerHTML = content;
        } else if (content instanceof HTMLElement) {
            this.contentElement.innerHTML = '';
            this.contentElement.appendChild(content);
        }
    }
    
    /**
     * モーダルを破棄
     */
    destroy() {
        this.close();
        
        // イベントリスナーの削除
        if (this._escapeHandler) {
            document.removeEventListener('keydown', this._escapeHandler);
        }
        
        // DOM要素の削除
        setTimeout(() => {
            if (this.overlayElement) {
                this.overlayElement.remove();
            }
            if (this.modalElement) {
                this.modalElement.remove();
            }
        }, this.options.animationDuration);
    }
}

// 簡易的なモーダル表示関数
function showModal(options) {
    const modal = new Modal();
    modal.open(options);
    return modal;
}

// エクスポート（モジュール環境の場合）
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { Modal, showModal };
}