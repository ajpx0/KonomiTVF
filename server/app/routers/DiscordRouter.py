import base64
import hashlib
import secrets
import time
from typing import Annotated, TypedDict
from urllib.parse import urlencode

import httpx2 as httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app import logging, schemas
from app.constants import (
    API_REQUEST_HEADERS,
    DISCORD_CLIENT_ID,
    DISCORD_SOCIAL_SDK_PATH,
    HTTPX_CLIENT,
)
from app.models.User import User
from app.routers.UsersRouter import GetCurrentUser
from app.utils.DiscordSocialSDKClient import DiscordSocialSDKClient
from app.utils.OAuthCallbackResponse import OAuthCallbackResponse


# ルーター
router = APIRouter(
    tags = ['Discord'],
    prefix = '/api/discord',
)


class PendingAuthorization(TypedDict):
    """ OAuth 認証の開始からコールバックまでの間、サーバー側で保持しておく情報 """
    user_id: int
    code_verifier: str
    redirect_uri: str
    client_url: str
    created_at: float


# 認証 URL を発行してからコールバックされるまでの、state をキーにした認証情報
## PKCE の code_verifier はブラウザに渡さずサーバー内だけで保持する必要があるため、メモリ上に置く
## サーバーが再起動した場合は消えるが、その場合はもう一度連携し直してもらえばよい
PENDING_AUTHORIZATIONS: dict[str, PendingAuthorization] = {}

# 認証 URL を発行してから、コールバックを受け付ける期限 (秒)
AUTHORIZATION_TIMEOUT = 600

# 要求するスコープ
## identify: 連携したアカウントの名前とアイコンを設定画面に表示するために使う
## openid / sdk.social_layer_presence: Discord Social SDK で Rich Presence を設定するのに必要
DISCORD_OAUTH_SCOPES = 'identify openid sdk.social_layer_presence'


@router.get(
    '/auth',
    summary = 'Discord OAuth 認証 URL 発行 API',
    response_model = schemas.ThirdpartyAuthURL,
    response_description = 'ユーザーにアプリ連携してもらうための認証 URL。',
)
async def DiscordAuthURLAPI(
    request: Request,
    current_user: Annotated[User, Depends(GetCurrentUser)],
):
    """
    Discord アカウントと連携するための認証 URL を取得する。<br>
    認証 URL をブラウザで開くとアプリ連携の許可を求められ、ユーザーが許可すると /api/discord/callback に戻ってくる。

    JWT エンコードされたアクセストークンがリクエストの Authorization: Bearer に設定されていないとアクセスできない。
    """

    # 連携しても Discord Social SDK がなければ Rich Presence を設定できないため、先にエラーにする
    if DiscordSocialSDKClient.isAvailable() is False:
        logging.error(f'[DiscordRouter][DiscordAuthURLAPI] Discord Social SDK is not available. ({DISCORD_SOCIAL_SDK_PATH})')
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Discord Social SDK is not installed',
        )

    # 期限切れの認証情報を掃除する
    now = time.time()
    for state, pending in list(PENDING_AUTHORIZATIONS.items()):
        if now - pending['created_at'] > AUTHORIZATION_TIMEOUT:
            del PENDING_AUTHORIZATIONS[state]

    # PKCE の code_verifier と、そこから導出した code_challenge (S256) を生成する
    ## Public Client として OAuth 認証するため、クライアントシークレットの代わりに PKCE を使う
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode('ascii')).digest()).decode('ascii').rstrip('=')

    # コールバック先はこの KonomiTV サーバー自身
    ## Discord Developer Portal の OAuth2 Redirects に、このコールバック URL を登録しておく必要がある
    redirect_uri = f'https://{request.url.netloc}/api/discord/callback'

    # 認証情報を state をキーにして保持しておく
    state = secrets.token_urlsafe(32)
    PENDING_AUTHORIZATIONS[state] = PendingAuthorization(
        user_id = current_user.id,
        code_verifier = code_verifier,
        redirect_uri = redirect_uri,
        # スマホ・タブレットでの DiscordAuthCallbackAPI のリダイレクト先 URL
        client_url = request.headers.get('Origin', f'https://{request.url.netloc}').rstrip('/'),
        created_at = now,
    )

    # 認証 URL を作成
    authorization_url = 'https://discord.com/oauth2/authorize?' + urlencode({
        'client_id': DISCORD_CLIENT_ID,
        'response_type': 'code',
        'redirect_uri': redirect_uri,
        'scope': DISCORD_OAUTH_SCOPES,
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
    })

    return {'authorization_url': authorization_url}


@router.get(
    '/callback',
    summary = 'Discord OAuth コールバック API',
    response_class = OAuthCallbackResponse,
    response_description = 'ユーザーアカウントに Discord アカウントのアクセストークン・リフレッシュトークンが登録できたことを示す。',
)
async def DiscordAuthCallbackAPI(
    state: Annotated[str, Query(description='認証 URL の発行時に生成した state 。')],
    code: Annotated[str | None, Query(description='コールバック元から渡された認証コード。OAuth 認証が成功したときのみセットされる。')] = None,
    error: Annotated[str | None, Query(description='このパラメーターがセットされているとき、OAuth 認証がユーザーによって拒否されたことを示す。')] = None,
):
    """
    Discord の OAuth 認証のコールバックを受け取り、ログイン中のユーザーアカウントと Discord アカウントを紐づける。
    """

    # state に対応する認証情報を取り出す (同じ state は二度使えない)
    pending = PENDING_AUTHORIZATIONS.pop(state, None)

    # 認証情報がない or 期限切れ
    ## リダイレクト先が分からないため、このサーバーの設定画面に戻す
    if pending is None or time.time() - pending['created_at'] > AUTHORIZATION_TIMEOUT:
        logging.warning('[DiscordRouter][DiscordAuthCallbackAPI] Authorization state is invalid or expired.')
        return OAuthCallbackResponse(
            status_code = status.HTTP_400_BAD_REQUEST,
            detail = 'Authorization state is invalid or expired',
            redirect_to = '/settings/general',
        )

    # スマホ・タブレット向けのリダイレクト先 URL を生成
    redirect_url = f'{pending["client_url"]}/settings/general'

    # "error" パラメーターがセットされている
    # OAuth 認証がユーザーによって拒否されたことを示しているので、401 エラーにする
    if error is not None or code is None:
        logging.warning(f'[DiscordRouter][DiscordAuthCallbackAPI] Authorization was denied. ({error})')
        return OAuthCallbackResponse(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = f'Authorization was denied ({error})',
            redirect_to = redirect_url,
        )

    # 認証を開始したユーザーアカウントを取得
    current_user = await User.filter(id=pending['user_id']).get_or_none()
    if current_user is None:
        return OAuthCallbackResponse(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = 'User not found',
            redirect_to = redirect_url,
        )

    # 認証コードと PKCE の code_verifier を使い、アクセストークンとユーザー情報を取得してユーザーアカウントに保存する
    try:
        await current_user.requestDiscordToken({
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': pending['redirect_uri'],
            'code_verifier': pending['code_verifier'],
        })
    except Exception as ex:
        logging.error(f'[DiscordRouter][DiscordAuthCallbackAPI] {ex}')
        return OAuthCallbackResponse(
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail = str(ex),
            redirect_to = redirect_url,
        )

    # OAuth 連携が正常に完了したことを伝える
    return OAuthCallbackResponse(
        status_code = status.HTTP_200_OK,
        detail = 'Success',
        redirect_to = redirect_url,
    )


@router.delete(
    '/logout',
    summary = 'Discord アカウント連携解除 API',
    status_code = status.HTTP_204_NO_CONTENT,
)
async def DiscordAccountLogoutAPI(
    current_user: Annotated[User, Depends(GetCurrentUser)],
):
    """
    現在ログイン中のユーザーアカウントに紐づく Discord アカウントとの連携を解除する。<br>
    JWT エンコードされたアクセストークンがリクエストの Authorization: Bearer に設定されていないとアクセスできない。
    """

    # Discord 側のトークンも失効させておく
    ## 失敗しても KonomiTV 側の連携解除には影響しないため、結果は確認しない
    if current_user.discord_refresh_token is not None:
        try:
            async with HTTPX_CLIENT() as httpx_client:
                await httpx_client.post(
                    url = 'https://discord.com/api/v10/oauth2/token/revoke',
                    headers = {**API_REQUEST_HEADERS, 'Content-Type': 'application/x-www-form-urlencoded'},
                    data = {
                        'client_id': DISCORD_CLIENT_ID,
                        'token': current_user.discord_refresh_token,
                        'token_type_hint': 'refresh_token',
                    },
                )
        except (httpx.NetworkError, httpx.TimeoutException):
            pass

    # Discord 関連のフィールドをすべて None (null) にすることで連携解除とする
    await current_user.clearDiscordAccount()
