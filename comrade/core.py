import typing

import attr
import cattr
import grpc
import toml

from discord.ext.commands import Bot as _Bot
from discord_slash import SlashCommand
from grpc_reflection.v1alpha import reflection
from sqlalchemy.ext.asyncio import create_async_engine

from comrade import db


EXTENSIONS = [".core", ".dkp"]


@attr.s(slots=True, auto_attribs=True)
class Listener:

    bind: str
    tls_certificate: typing.Optional[str] = None
    tls_certificate_key: typing.Optional[str] = None
    tls_trusted_certificates: typing.Optional[str] = None
    require_client_auth: bool = False


@attr.s(slots=True, auto_attribs=True)
class RPC:

    listeners: typing.List[Listener] = attr.ib(factory=list)


@attr.s(slots=True, auto_attribs=True)
class DKP:

    url: str
    database: str
    dkp_pool_id: int


@attr.s(slots=True, auto_attribs=True)
class Discord:

    token: str
    server_id: int


@attr.s(slots=True, auto_attribs=True)
class AuctionRoles:

    officer: str
    raider: str
    recruit: str
    member: str


@attr.s(slots=True, auto_attribs=True)
class AuctionLimits:

    valuable: int
    member: int
    minimum: int
    maximum: int


@attr.s(slots=True, auto_attribs=True)
class Auction:

    roles: AuctionRoles
    limits: AuctionLimits
    channels: list[str] = attr.ib(factory=list)


@attr.s(slots=True, auto_attribs=True)
class Config:

    database: str
    discord: Discord
    dkp: DKP
    auction: Auction
    rpc: RPC = attr.ib(factory=RPC)


class Bot(_Bot):
    def __init__(self, command_prefix="!", *args, config_file, **kwargs):
        super().__init__(command_prefix, *args, **kwargs)

        self._slash = SlashCommand(self, sync_commands=True)

        with open(config_file) as fp:
            self.config: Config = cattr.structure(toml.load(fp), Config)

        self.db = create_async_engine(self.config.database)

        self.rpc = grpc.aio.server()
        self._rpc_services = []

        for listener in self.config.rpc.listeners:
            if all([listener.tls_certificate, listener.tls_certificate_key]):
                grpc.ssl_server_credentials(
                    [(listener.tls_certificate_key, listener.tls_certificate)],
                    root_certificates=listener.tls_trusted_certificates,
                    require_client_auth=listener.require_client_auth,
                )
                self.rpc.add_secure_port(listener.bind, None)
            else:
                self.rpc.add_insecure_port(listener.bind)

        for ext in EXTENSIONS:
            self.load_extension(ext, package="comrade.plugins")

    def add_rpc(self, servicer, name, register_cb):
        self._rpc_services.append(name)
        register_cb(servicer, self.rpc)

    def run(self, token=None, *args, **kwargs):
        reflection.enable_server_reflection(self._rpc_services, self.rpc)

        if token is None:
            token = self.config.discord.token

        return super().run(token, *args, **kwargs)

    async def start(self, *args, **kwargs):
        await self.rpc.start()
        return await super().start(*args, **kwargs)

    async def close(self, *args, **kwargs):
        await self.rpc.stop(10)
        return await super().close(*args, **kwargs)

    async def on_ready(self):
        async with self.db.begin() as conn:
            await conn.run_sync(db.metadata.create_all)
