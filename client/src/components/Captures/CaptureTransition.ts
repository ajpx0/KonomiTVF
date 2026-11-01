
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
 * 拡大表示の開閉で、タイルと1枚表示の間を画像が拡大・縮小していくアニメーションを行うかどうかを返す
 * 動きを減らす設定のときは、アニメーションせずにフェードだけで開閉する
 * @returns アニメーションさせるなら true
 */
export function canAnimateCaptureImage(): boolean {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches === false;
}


/**
 * 画像の複製を1枚だけ画面の最前面に置き、from の位置と大きさから to の位置と大きさまで動かす
 * 拡大表示の開閉で、タイルの画像と1枚表示の画像をつなぐために使う
 * 動かすのはこの複製の1枚だけなので、後ろの一覧や拡大表示を描き直さずに済む
 * 複製は動き終わっても残すので、呼び出し元が後ろの画像を表示し直してから element.remove() で取り除く
 * @param src 画像の URL (タイルと1枚表示は同じ原寸の画像を表示しているので、読み込み済みのものが使われる)
 * @param from 動き始める位置と大きさ
 * @param to 動き終わる位置と大きさ
 * @returns 複製の画像と、動き終わったときに解決される Promise
 */
export function flyCaptureImage(src: string, from: DOMRect, to: DOMRect): { element: HTMLImageElement; finished: Promise<void> } {
    const element = document.createElement('img');
    element.className = 'capture-flying-image';
    element.src = src;
    element.alt = '';
    document.body.append(element);

    // タイルは画像を切り抜いて並べているので、縦横比の違いで画像が伸び縮みしないよう、位置と大きさを動かして切り抜いたまま表示する
    const toKeyframe = (rect: DOMRect) => ({
        left: `${rect.left}px`,
        top: `${rect.top}px`,
        width: `${rect.width}px`,
        height: `${rect.height}px`,
    });
    const animation = element.animate([toKeyframe(from), toKeyframe(to)], {
        duration: 200,
        easing: 'cubic-bezier(0.26, 0.68, 0.55, 0.99)',
        fill: 'forwards',
    });
    return { element, finished: animation.finished.then(() => undefined, () => undefined) };
}


/**
 * 拡大表示を開くときに、タイルの画像が1枚表示の画像の位置まで拡大していくように見せる
 * 拡大表示の暗幕・ボタン・情報欄は、CaptureLightbox.vue の Vue のトランジションで同時にフェードインする
 * @param tileImage タイルの画像 (null なら、画像の拡大なしにフェードインだけで開く)
 * @param open 拡大表示を開く処理
 */
export function openWithTransition(tileImage: HTMLImageElement | null, open: () => void): void {
    if (tileImage === null || canAnimateCaptureImage() === false) {
        open();
        return;
    }
    const from = tileImage.getBoundingClientRect();
    open();
    void nextTick(async () => {
        const target = document.querySelector<HTMLImageElement>('.capture-lightbox__image--current');
        if (target === null) {
            return;
        }

        // 拡大していく間は、1枚表示の画像とタイルの画像を隠し、複製の1枚だけを見せる
        // 1枚表示の画像の大きさはキャプチャの縦横の大きさから決まっているので、原寸の画像の読み込みを待たずに行き先が分かる
        const flight = flyCaptureImage(tileImage.currentSrc || tileImage.src, from, target.getBoundingClientRect());
        target.style.visibility = 'hidden';
        tileImage.style.visibility = 'hidden';
        await flight.finished;

        // 1枚表示の画像のデコードが終わってから入れ替え、入れ替えの瞬間に画像が消えて見えないようにする (数百ミリ秒で諦めて入れ替える)
        await Promise.race([
            target.decode().catch(() => undefined),
            new Promise(resolve => window.setTimeout(resolve, 300)),
        ]);
        target.style.removeProperty('visibility');
        tileImage.style.removeProperty('visibility');
        flight.element.remove();
    });
}
