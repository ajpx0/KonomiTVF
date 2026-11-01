import Template from './template';

const isMobile = /iPhone|iPad|iPod|Windows|Macintosh|Android|Mobile/i.test(navigator.userAgent) && 'ontouchend' in document;

const utils = {
    /**
     * Parse second to time string
     *
     * @param {Number} second
     * @return {String} 00:00 or 00:00:00
     */
    secondToTime: (second: number): string => {
        second = second || 0;
        if (second === 0 || second === Infinity || second.toString() === 'NaN') {
            return '00:00';
        }
        const add0 = (num: number): string => num < 10 ? '0' + num : '' + num;
        const hour = Math.floor(second / 3600);
        const min = Math.floor((second - hour * 3600) / 60);
        const sec = Math.floor(second - hour * 3600 - min * 60);
        return (hour > 0 ? [hour, min, sec] : [min, sec]).map(add0).join(':');
    },

    /**
     * get video duration
     * compatibility: measures against video length becoming Infinity during HLS playback on native HLS player of Safari
     *
     * @param {HTMLVideoElement} video
     * @param {Template} template
     * @returns {Number}
     */
    getVideoDuration: (video: HTMLVideoElement, template: Template): number => {
        let duration = video.duration;
        if (duration === Infinity) {
            if (video.seekable.length > 0) {
                template.dtime.textContent = utils.secondToTime(video.seekable.end(0));
                duration = video.seekable.end(0);
            } else if (video.buffered.length > 0) {
                template.dtime.textContent = utils.secondToTime(video.buffered.end(0));
                duration = video.buffered.end(0);
            }
        }
        return duration;
    },

    /**
     * Get the X coordinate of the pointer relative to a container element.
     * @param event - PointerEvent or TouchEvent.
     * @param container - The container to calculate relative coordinate for.
     * @returns The relative X coordinate in pixels.
     */
    getRelativeX: (event: PointerEvent | TouchEvent | MouseEvent, container: HTMLElement): number => {
        const rect = container.getBoundingClientRect();
        let clientX: number;
        let clientY: number;
        if ('touches' in event) {
            // If TouchEvent, use changedTouches if touches is empty
            if (event.touches.length > 0) {
                clientX = event.touches[0].clientX;
                clientY = event.touches[0].clientY;
            } else if (event.changedTouches && event.changedTouches.length > 0) {
                clientX = event.changedTouches[0].clientX;
                clientY = event.changedTouches[0].clientY;
            } else {
                // If both are not available, return 0 (should not happen normally)
                clientX = 0;
                clientY = 0;
            }
        } else {
            clientX = event.clientX;
            clientY = event.clientY;
        }
        // The container may be rotated by CSS transforms on its ancestors (e.g. pseudo fullscreen on iPhone Safari),
        // so measure the position along the container's own X axis instead of the screen X axis
        let angle = 0;
        for (let element: Element | null = container; element !== null; element = element.parentElement) {
            const transform = getComputedStyle(element).transform;
            if (transform && transform !== 'none') {
                const matrix = new DOMMatrixReadOnly(transform);
                angle += Math.atan2(matrix.b, matrix.a);
            }
        }
        const quarterTurns = (((Math.round(angle / (Math.PI / 2)) % 4) + 4) % 4);
        switch (quarterTurns) {
            case 1:
                return clientY - rect.top;
            case 2:
                return rect.right - clientX;
            case 3:
                return rect.bottom - clientY;
            default:
                return clientX - rect.left;
        }
    },

    getScrollPosition(): { left: number; top: number } {
        return {
            left: window.pageXOffset || document.documentElement.scrollLeft || document.body.scrollLeft || 0,
            top: window.pageYOffset || document.documentElement.scrollTop || document.body.scrollTop || 0,
        };
    },

    setScrollPosition({ left = 0, top = 0 }: { left: number; top: number; }): void {
        if (this.isFirefox) {
            document.documentElement.scrollLeft = left;
            document.documentElement.scrollTop = top;
        } else {
            window.scrollTo(left, top);
        }
    },

    isMobile: isMobile,

    isFirefox: /firefox/i.test(window.navigator.userAgent),

    // ref: https://rnwest.engineer/detect-webkit/
    isWebKit: (/AppleWebKit/.test(window.navigator.userAgent) && !/Chrome/.test(window.navigator.userAgent)) ||
        /\b(iPad|iPhone|iPod)\b/.test(window.navigator.userAgent),

    storage: {
        set: (key: string, value: any): void => {
            localStorage.setItem(key, value);
        },

        get: (key: string): string | null => localStorage.getItem(key),
    },

    nameMap: {
        dragStart: isMobile ? 'touchstart' : 'mousedown',
        dragMove: isMobile ? 'touchmove' : 'mousemove',
        dragEnd: isMobile ? 'touchend' : 'mouseup',
    },

    // currently not used
    color2Number: (color: string): number => {
        if (color[0] === '#') {
            color = color.substr(1);
        }
        if (color.length === 3) {
            color = `${color[0]}${color[0]}${color[1]}${color[1]}${color[2]}${color[2]}`;
        }
        return (parseInt(color, 16) + 0x000000) & 0xffffff;
    },

    number2Color: (number: number): string => '#' + ('00000' + number.toString(16)).slice(-6),

    number2Type: (number: number): string => {
        switch (number) {
            case 0:
                return 'right';
            case 1:
                return 'top';
            case 2:
                return 'bottom';
            default:
                return 'right';
        }
    },
};

export default utils;
