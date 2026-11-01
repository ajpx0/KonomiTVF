
# Type Hints を指定できるように
# ref: https://stackoverflow.com/a/33533514/17124142
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import httpx2 as httpx
from tortoise import fields
from tortoise.fields import Field as TortoiseField
from tortoise.models import Model as TortoiseModel

from app.constants import (
    API_REQUEST_HEADERS,
    DISCORD_CLIENT_ID,
    HTTPX_CLIENT,
    NICONICO_OAUTH_CLIENT_ID,
)
from app.utils import Interlaced


if TYPE_CHECKING:
    from app.models.AccountLink import AccountLink
    from app.models.BlueskyAccount import BlueskyAccount
    from app.models.Capture import CaptureFavorite, CaptureTag
    from app.models.TwitterAccount import TwitterAccount


class User(TortoiseModel):

    # データベース上のテーブル名
    class Meta(TortoiseModel.Meta):
        table: str = 'users'

    id = fields.IntField(primary_key=True)
    name = fields.TextField()
    password = fields.TextField()
    is_admin = fields.BooleanField()
    client_settings = cast(TortoiseField[dict[str, Any]], fields.JSONField(default={}, encoder=lambda x: json.dumps(x, ensure_ascii=False)))  # type: ignore
    niconico_user_id = cast(TortoiseField[int | None], fields.IntField(null=True))
    niconico_user_name = cast(TortoiseField[str | None], fields.TextField(null=True))
    niconico_user_premium = cast(TortoiseField[bool | None], fields.BooleanField(null=True))
    niconico_access_token = cast(TortoiseField[str | None], fields.TextField(null=True))
    niconico_refresh_token = cast(TortoiseField[str | None], fields.TextField(null=True))
    discord_user_id = cast(TortoiseField[str | None], fields.TextField(null=True))
    discord_user_name = cast(TortoiseField[str | None], fields.TextField(null=True))
    discord_user_icon_url = cast(TortoiseField[str | None], fields.TextField(null=True))
    discord_access_token = cast(TortoiseField[str | None], fields.TextField(null=True))
    discord_refresh_token = cast(TortoiseField[str | None], fields.TextField(null=True))
    capture_favorites: fields.ReverseRelation[CaptureFavorite]
    capture_tags: fields.ReverseRelation[CaptureTag]
    twitter_accounts: fields.ReverseRelation[TwitterAccount]
    bluesky_accounts: fields.ReverseRelation[BlueskyAccount]
    account_links: fields.ReverseRelation[AccountLink]
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)


    async def refreshNiconicoAccessToken(self) -> None:
        """
        このユーザーに紐づくニコニコアカウントのアクセストークンを、リフレッシュトークンで更新する
        更新されたアクセストークンはこのメソッド内でデータベースに永続化される

        Raises:
            Exception: アクセストークンの更新に失敗した場合 (例外に含まれるエラーメッセージを API レスポンスで返す想定)
        """

        try:

            # リフレッシュトークンを使い、ニコニコ OAuth のアクセストークンとリフレッシュトークンを更新
            token_api_url = 'https://oauth.nicovideo.jp/oauth2/token'
            async with HTTPX_CLIENT() as client:
                token_api_response = await client.post(
                    url = token_api_url,
                    headers = {**API_REQUEST_HEADERS, 'Content-Type': 'application/x-www-form-urlencoded'},
                    data = {
                        'grant_type': 'refresh_token',
                        'client_id': NICONICO_OAUTH_CLIENT_ID,
                        'client_secret': Interlaced(3),
                        'refresh_token': self.niconico_refresh_token,
                    },
                )

            # ステータスコードが 200 以外
            if token_api_response.status_code != 200:
                error_code = ''
                try:
                    error_code = f' ({token_api_response.json()["error"]})'
                except Exception:
                    pass
                raise Exception(f'アクセストークンの更新に失敗しました。(HTTP Error {token_api_response.status_code}{error_code})')

            token_api_response_json = token_api_response.json()

        # 接続エラー（サーバーメンテナンスやタイムアウトなど）
        except (httpx.NetworkError, httpx.TimeoutException):
            raise Exception('アクセストークンの更新リクエストがタイムアウトしました。')

        # 取得したアクセストークンとリフレッシュトークンをユーザーアカウントに設定
        ## 仕様上リフレッシュトークンに有効期限はないが、一応このタイミングでリフレッシュトークンも更新することが推奨されている
        self.niconico_access_token = str(token_api_response_json['access_token'])
        self.niconico_refresh_token = str(token_api_response_json['refresh_token'])

        try:
            # ついでなので、このタイミングでユーザー情報を取得し直す
            ## 頻繁に変わるものでもないとは思うけど、一応再ログインせずとも同期されるようにしておきたい
            ## 3秒応答がなかったらタイムアウト
            user_api_url = f'https://nvapi.nicovideo.jp/v1/users/{self.niconico_user_id}'
            async with HTTPX_CLIENT() as client:
                # X-Frontend-Id がないと INVALID_PARAMETER になる
                user_api_response = await client.get(user_api_url, headers={**API_REQUEST_HEADERS, 'X-Frontend-Id': '6'})

            if user_api_response.status_code == 200:
                # ユーザー名
                self.niconico_user_name = str(user_api_response.json()['data']['user']['nickname'])
                # プレミアム会員かどうか
                self.niconico_user_premium = bool(user_api_response.json()['data']['user']['isPremium'])

        # 接続エラー（サーバー再起動やタイムアウトなど）
        except (httpx.NetworkError, httpx.TimeoutException):
            pass  # 取れなくてもセッション取得に支障はないのでパス

        # 変更をデータベースに保存
        await self.save()


    async def requestDiscordToken(self, grant_data: dict[str, str]) -> None:
        """
        Discord OAuth のトークンエンドポイントからアクセストークン・リフレッシュトークンを取得し、このユーザーに紐づく Discord アカウントの情報を更新する
        認証コードでの初回取得とリフレッシュトークンでの更新の両方で使い、ユーザー情報も取得し直してデータベースに永続化する
        リフレッシュトークンが失効していた (連携が Discord 側で解除された) 場合は、Discord アカウントとの連携を解除する

        Args:
            grant_data (dict[str, str]): grant_type とその種類ごとに必要なパラメーター (client_id 以外)

        Raises:
            Exception: トークンまたはユーザー情報の取得に失敗した場合 (例外に含まれるエラーメッセージを API レスポンスで返す想定)
        """

        try:
            # Public Client として登録しているため、クライアントシークレットは不要
            async with HTTPX_CLIENT() as client:
                token_api_response = await client.post(
                    url = 'https://discord.com/api/v10/oauth2/token',
                    headers = {**API_REQUEST_HEADERS, 'Content-Type': 'application/x-www-form-urlencoded'},
                    data = {**grant_data, 'client_id': DISCORD_CLIENT_ID},
                )
        # 接続エラー（サーバーメンテナンスやタイムアウトなど）
        except (httpx.NetworkError, httpx.TimeoutException):
            raise Exception('Failed to get access token (Connection Timeout)')

        # リフレッシュトークンが失効している場合は、再連携してもらうしかないため連携を解除する
        if grant_data['grant_type'] == 'refresh_token' and token_api_response.status_code in (400, 401):
            await self.clearDiscordAccount()
            raise Exception(f'Refresh token was revoked (HTTP Error {token_api_response.status_code})')

        # ステータスコードが 200 以外
        if token_api_response.status_code != 200:
            raise Exception(f'Failed to get access token (HTTP Error {token_api_response.status_code})')

        # 取得したアクセストークンとリフレッシュトークンをユーザーアカウントに設定
        ## Discord のリフレッシュトークンは使うたびに新しいものに置き換わるため、毎回両方とも保存し直す
        token_response_json = token_api_response.json()
        self.discord_access_token = str(token_response_json['access_token'])
        self.discord_refresh_token = str(token_response_json['refresh_token'])

        try:
            # identify スコープで、連携した Discord アカウントのユーザー情報を取得
            async with HTTPX_CLIENT() as client:
                user_api_response = await client.get(
                    url = 'https://discord.com/api/v10/users/@me',
                    headers = {**API_REQUEST_HEADERS, 'Authorization': f'Bearer {self.discord_access_token}'},
                )
        # 接続エラー（サーバーメンテナンスやタイムアウトなど）
        except (httpx.NetworkError, httpx.TimeoutException):
            raise Exception('Failed to get user information (Connection Timeout)')

        # ステータスコードが 200 以外
        if user_api_response.status_code != 200:
            raise Exception(f'Failed to get user information (HTTP Error {user_api_response.status_code})')
        user_json = user_api_response.json()

        # ユーザー ID は 64bit 整数で JavaScript の Number では精度が落ちるため、文字列のまま保存する
        self.discord_user_id = str(user_json['id'])
        # 表示名が設定されていない場合はユーザー名を使う
        self.discord_user_name = str(user_json.get('global_name') or user_json['username'])
        # アイコンが設定されていない場合は、ユーザー ID から決まるデフォルトアイコンを使う
        if user_json.get('avatar'):
            self.discord_user_icon_url = f'https://cdn.discordapp.com/avatars/{self.discord_user_id}/{user_json["avatar"]}.png?size=256'
        else:
            self.discord_user_icon_url = f'https://cdn.discordapp.com/embed/avatars/{(int(self.discord_user_id) >> 22) % 6}.png'

        # 変更をデータベースに保存
        await self.save()


    async def clearDiscordAccount(self) -> None:
        """
        このユーザーに紐づく Discord アカウントとの連携を解除する
        Discord 関連のフィールドをすべて None (null) にしてデータベースに永続化する
        """

        self.discord_user_id = None
        self.discord_user_name = None
        self.discord_user_icon_url = None
        self.discord_access_token = None
        self.discord_refresh_token = None
        await self.save()
