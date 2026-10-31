
import { nextTick } from 'vue';


/**
 * 一覧のタイルの画像を返す (読み込み済みの画像だけ)
 * 拡大表示を開閉するときに、タイルの画像と1枚表示の画像をつなぐために使う
 * @param capture_id キャプチャの ID
 * @returns タイルの画像 (画面に描画されていないか、まだ読み込めていなければ null)
 */
export function findTileImage(capture_id: number): HTMLImageElement | null {
    return document.querySelector<HTMLImageElement>(`.capture-tile[data-capture-id="${capture_id}"] .capture-tile__image--loaded`);
}


/**
 * 拡大表示の開閉を View Transitions API でアニメーションさせるかどうかを返す
 * 対応していないブラウザと、動きを減らす設定のときは、アニメーションせずに開閉する
 * @returns アニメーションさせるなら true
 */
export function canUseViewTransition(): boolean {
    return document.startViewTransition !== undefined && window.matchMedia('(prefers-reduced-motion: reduce)').matches === false;
}


/**
 * 拡大表示を開くときに、View Transitions API でタイルの画像が1枚表示の画像の位置まで拡大していくように見せる
 * タイルの画像と1枚表示の画像に同じ名前を付けると、ブラウザが2つの位置と大きさをつないでアニメーションさせる
 * 拡大表示の暗幕・ボタン・情報欄は、画像の拡大と同時にフェードインさせる (CaptureLightbox.vue のスタイルを参照)
 * @param tileImage タイルの画像 (null なら、画像の拡大なしにフェードインだけで開く)
 * @param open 拡大表示を開く処理
 */
export function openWithTransition(tileImage: HTMLImageElement | null, open: () => void): void {
    if (document.startViewTransition === undefined || canUseViewTransition() === false) {
        open();
        return;
    }
    tileImage?.style.setProperty('view-transition-name', 'capture-lightbox-image');
    // 画面全体ではなく、拡大していく画像と拡大表示だけを撮らせる
    // 開くときは、原寸の画像のデコードを待たずに動き出し、拡大していく間はタイルの画像を引き伸ばして表示する
    // 原寸の画像のデコードには数十〜百ミリ秒かかり、待つとその間は画面が止まって見えるため
    document.documentElement.classList.add('capture-lightbox-transition', 'capture-lightbox-opening');
    const transition = document.startViewTransition(async () => {
        // 開いた後の画面では、名前を1枚表示の画像 (CaptureLightbox.vue) へ渡す
        tileImage?.style.removeProperty('view-transition-name');
        open();
        await nextTick();
    });
    void transition.finished.finally(() => {
        document.documentElement.classList.remove('capture-lightbox-transition', 'capture-lightbox-opening');
    });
}
