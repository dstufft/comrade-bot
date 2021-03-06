import enum
import datetime
import itertools
import logging
import random
import typing
import functools

from collections.abc import Iterable

import attr
import discord
import discord.utils
import humanize

from discord.ext import tasks
from discord.ext.commands import Cog
from discord_slash import cog_ext, SlashContext
from discord_slash.model import SlashCommandOptionType as OptionType
from discord_slash.utils.manage_commands import create_option

from .provider import CharacterDKP


logger = logging.getLogger(__name__)


def humanize_delta(td: datetime.timedelta) -> str:
    return humanize.precisedelta(td, format="%0.0f")


async def smart_send(ctx, hidden=False, **kwargs):
    # There's an issue (it might be with the library or the API, not sure which)
    # where if you defer a command with a hidden response, you can't then later
    # respond with a public response without first giving a private response.
    #
    # In addition to that, public responses after a private response show a crummy
    # UI where it tries to load the original message and you get either told it
    # was deleted OR it couldn't be loaded.
    #
    # To solve all of this, our smart_send function will send private responses
    # as responses to the command handler, but public responses will just be sent
    # directly to the channel.
    #
    # This only really needs to be used in situations where a command might send
    # a mixture of response types (hidden and public) after a ctx.defer.
    if hidden:
        await ctx.send(hidden=True, **kwargs)
    else:
        await ctx.channel.send(**kwargs)


@attr.s(slots=True, frozen=True, auto_attribs=True)
class AuctionItem:

    item: str
    quantity: int
    added_by: str

    @property
    def description(self) -> str:
        if self.quantity > 1:
            return f"{self.item} x{self.quantity}"
        else:
            return self.item


class Status(enum.Enum):
    def __repr__(self):
        return "<%s.%s>" % (self.__class__.__name__, self.name)

    Running = enum.auto()
    Stopped = enum.auto()
    Finished = enum.auto()


class BidderRank(enum.Enum):
    def __repr__(self):
        return "<%s.%s>" % (self.__class__.__name__, self.name)

    Raider = enum.auto()
    Alt = enum.auto()
    Recruit = enum.auto()
    Member = enum.auto()


@attr.s(slots=True, frozen=True, auto_attribs=True)
class Bid:

    bidder: str
    rank: BidderRank
    bid: int
    id: int = 0


@attr.s(slots=True, frozen=True, auto_attribs=True)
class AuctionResults:

    winners: list[Bid] = attr.ib(factory=list)
    tied: list[Bid] = attr.ib(factory=list)
    rolled: int = 0


@attr.s(slots=True, auto_attribs=True)
class RunningAuction:

    item: AuctionItem
    status: Status = Status.Running
    started_at: datetime.datetime = attr.ib(factory=datetime.datetime.utcnow)
    last_bid: typing.Optional[datetime.datetime] = None
    last_updated: typing.Optional[datetime.datetime] = None
    bids: set[Bid] = attr.ib(factory=set)
    results: typing.Optional[AuctionResults] = None

    @property
    def time_left(self) -> datetime.timedelta:
        now = datetime.datetime.utcnow()

        # The logic here is kind of convulted, but it's basically inteded to roughly
        # encode the following rules:
        #
        # 1. Every auction must last a minimum of 90 seconds.
        # 2. Every auction must last at least 30 seconds since the last bid.
        # 3. Every auction must last at least 15 seconds since the last update.
        # 4. Every auction must not end without a final update (with included 15s).
        #
        # In the end, since we don't have precise control over when things get
        # processed, particularly with the #4 rule above, we don't know exactly
        # when the auction is going to end (until it's time to end it), but we
        # know that it will be AT LEAST this amount of time, which is close enough.

        # We'll start with the 90s minimum.
        end = self.started_at + datetime.timedelta(seconds=90)

        # Next we'll check to see what our end time is bsed off the last bid, if
        # we've had any bids, if that's further in the future then our default, then
        # that becomes our new end.
        if self.last_bid is not None:
            bid_end = self.last_bid + datetime.timedelta(seconds=30)
            if bid_end > end:
                end = bid_end

        # This bit is the most convulted part of all of this, because it has to deal
        # with multiple states that may or may not exist.
        #
        # This roughly translates into if we've ever updated, and we've either never had
        # a bid, or the update was after the last bid.
        if self.last_updated is not None and (
            self.last_bid is None or self.last_updated > self.last_bid
        ):
            updated_end = self.last_updated + datetime.timedelta(seconds=15)
            if updated_end > end:
                end = updated_end

        # If we've never updated, or we've updated but a bid has occured since then
        # then we don't know for sure when the bid is going to be able to be closed,
        # since we need an update for that. However we know it will be atleast 15s
        # from now, since if we updated *right* now, we would have at least a 15s
        # window. If that's more than we'd otherwise have, we'll shift the end time.
        if self.last_updated is None or (
            self.last_bid is not None and self.last_updated < self.last_bid
        ):
            updated_end = now + datetime.timedelta(seconds=15)
            if updated_end > end:
                end = updated_end

        # We finally know when we expect the auction to end, so we'll see if that's
        # inthe future or not. If it is not in the future, then our remaining time
        # is 0, otherwise we'll return the remaining time.
        if end > now:
            return end - now
        else:
            return datetime.timedelta(seconds=0)

    @property
    def needs_update(self) -> bool:
        # We can check to see if the auction is in anything but a running starte, if it
        # is, then we do not need an update.
        if self.status is not Status.Running:
            return False

        # Basic rules here are:
        # 1. If the auction started > 30s ago
        # 2. If the last bid was > 10s ago
        # 3. If the last update was > 30s ago
        now = datetime.datetime.utcnow()
        if (
            (now - self.started_at).total_seconds() > 30
            and (self.last_bid is None or (now - self.last_bid).total_seconds() > 10)
            and (
                self.last_updated is None
                or (now - self.last_updated).total_seconds() > 30
            )
        ):
            return True

        return False


@attr.s(slots=True, frozen=True, auto_attribs=True)
class AuctionMessage:

    channel: str
    message: typing.Union[str, discord.Embed]
    hidden: bool = False

    def as_kwargs(self):
        if isinstance(self.message, discord.Embed):
            return {"embed": self.message}
        else:
            return {"content": self.message}


def validate_bid(
    bidder: str,
    bid_amount: int,
    bids: Iterable[Bid],
    dkp: typing.Mapping[str, CharacterDKP],
    *,
    valuable_threshold: int,
    minimum: int,
    maximum: int,
) -> tuple[bool, str]:
    # Basic rules for a valid bid:
    #   1. If the bids are < the valuable threshold, then bids can go up
    #      by 1.
    #   2. If the bid is >= the valuable threshold, then the bid must be
    #      divisble by 5, UNLESS it's an All In Bid, OR matching an All In Bid.
    #   3. A player cannot bid > the maximum threshold.
    #   4. A player cannot bid < the minimum threshold.
    #   5. Player's cannot bid more than they have.
    bidder_dkp = dkp.get(bidder, CharacterDKP(name=bidder))
    if bid_amount > maximum:
        return (
            False,
            f"Error: Invalid Bid (bids above {maximum} are not allowed).",
        )
    elif bid_amount < minimum:
        return (False, f"Error: Invalid bid (bids below {minimum} are not allowed).")
    elif bidder_dkp.current == bid_amount:
        # We do nothing here, because this exists just so that we don't reject
        # an "all in" bid because it doesn't match the "divisble by 5" rules.
        pass
    elif bid_amount in {bid.bid for bid in bids}:
        # Again we do nothing here, because this only exists to prevent us from
        # progressing further down the elif chain, and allowing bids that match
        # already existing bids.
        pass
    elif bid_amount >= valuable_threshold and bid_amount % 5:
        return (
            False,
            (
                f"Error: Invalid Bid (bids above {valuable_threshold} must "
                f"be in increments of 5)."
            ),
        )

    # Now that we've made sure the bid is a valid number, we'll make sure that the
    # bidder has enough DKP to actually bid that amount.
    if bid_amount > bidder_dkp.current:
        return (False, "Error: Invalid Bid (not enough dkp).")
    else:
        return (True, "")


def _bid_key(dkp: typing.Mapping[str, CharacterDKP], member_treshold: int):
    def key_fn(bid: Bid):
        # Returns a tuple, this tuple is used to sort all of our bids, bids
        # that are worse should compare lower to bids that are better.
        #
        # Current Rules:
        #  1. When a bid is >= valuable_threshold, Mains are higher priority than
        #     anyone else.
        #  2. When a bid is < valuable_threshold, everyone is of equal priority.
        #  3. Smaller Bids are lower priority than higher bids.
        return (
            1 if bid.bid >= member_treshold and bid.rank is BidderRank.Raider else 0,
            bid.bid,
            dkp.get(bid.bidder, CharacterDKP(name=bid.bidder)).current,
        )

    return key_fn


def _filter_bids(bids: Iterable[Bid]) -> Iterable[Bid]:
    seen = set()
    for bid in bids:
        key = (bid.bidder, bid.id)
        if key not in seen:
            seen.add(key)
            yield bid


def determine_results(
    auction: RunningAuction,
    dkp: typing.Mapping[str, CharacterDKP],
    *,
    member_treshold=0,
) -> AuctionResults:
    # This function *MUST NOT* modify the running auction, it should just
    # indicate what the results would be, if it ended right now (which, if the
    # auction has ended, that is the actual result).
    need = auction.item.quantity
    winners = []
    tied = []
    rolled = 0

    all_bids = _filter_bids(
        sorted(auction.bids, key=_bid_key(dkp, member_treshold), reverse=True)
    )
    for _, b in itertools.groupby(all_bids, _bid_key(dkp, member_treshold)):
        bids = list(b)

        # If the number of people at this bid+current dkp doesn't exceed the
        # number of items we have left to assign, then we can just award it to
        # all of them, and reduce the amount needed by that amount.
        if len(bids) <= need:
            winners.extend(bids)
            need -= len(bids)

            # If we don't have any more itms to assign, then we're done looking
            # for winners.
            if not need:
                break
        # If we have more people at this bid+current dkp, then we need to just
        # call it a tie, and have those people roll off.
        else:
            tied.extend(bids)
            break

    # If we made it the through all of our bids, and we didn't find enough winners
    # then we return the rest of them as rolls.
    if need:
        rolled = need

    return AuctionResults(winners=winners, tied=tied, rolled=rolled)


def check_auction_channels(fn):
    @functools.wraps(fn)
    def wrapper(self, channel, *args, **kwargs):
        # Check if our bid is coming in on a channel that is one of our auction
        # channels. We can't scope the bid command to certain channels, so it could
        # happen on any of them.
        if channel not in self._channels:
            yield AuctionMessage(
                channel=channel,
                message="This isn't an auction channel. Try Again.",
                hidden=True,
            )
        # Likewise, even if it is one of our channels, there might not be an active
        # auction happening in that channel.
        elif self._channels[channel] is None:
            yield AuctionMessage(
                channel=channel,
                message="There isn't an active auction in this channel.",
                hidden=True,
            )
        else:
            yield from fn(self, channel, *args, **kwargs)

    return wrapper


def check_auction_status(statuses):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, channel, *args, **kwargs):
            for status, message in statuses.items():
                if self._channels[channel].status is status:
                    yield AuctionMessage(
                        channel=channel,
                        message=message,
                        hidden=True,
                    )
                    return
            yield from fn(self, channel, *args, **kwargs)

        return wrapper

    return deco


class Auctioneer:
    def __init__(self, *args, channels, limits, **kwargs):
        super().__init__(*args, *kwargs)

        self._pending_items: list[AuctionItem] = []
        self._channels: dict[str, typing.Optional[RunningAuction]] = {
            channel: None for channel in channels
        }
        self._limits = limits
        self._dkp: typing.Mapping[str, CharacterDKP] = {}

    @property
    def has_running_auctions(self) -> bool:
        return any(self._channels.values())

    def add(self, item: AuctionItem) -> None:
        self._pending_items.append(item)

    def update_dkp(self, dkp):
        self._dkp.clear()
        self._dkp.update(dkp)

    def run(self) -> Iterable[AuctionMessage]:
        # Loop over any running auctions we have, posting updates and/or closing the
        # auction as needed.
        for channel, auction in self._channels.items():
            # If there's no running auction here, we can just skip this channel.
            if auction is None:
                logger.debug(f"No auction for channel: {channel}, skipping.")
                continue

            # If this auction is ready to be closed, then we're going to close it.
            # This has to come before anything else we do, because we don't want
            # to update, then immediately close.
            if not auction.time_left and auction.status is Status.Running:
                auction.status = Status.Finished
                auction.results = determine_results(
                    auction, self._dkp, member_treshold=self._limits.member
                )
                yield AuctionMessage(
                    channel=channel,
                    message=f"Auction Closed. Results: {auction.results}",
                )

            # Check to see if we need to post an update for this auction to the
            # channel.
            if auction.needs_update:
                auction.last_updated = datetime.datetime.utcnow()
                results = determine_results(
                    auction, self._dkp, member_treshold=self._limits.member
                )
                yield AuctionMessage(
                    channel=channel,
                    message=(
                        f"This is an update for {auction.item.description} "
                        f"ending in {humanize_delta(auction.time_left)}.\n"
                        f"Results: {results}"
                    ),
                )

    @check_auction_channels
    @check_auction_status(
        {
            Status.Finished: (
                "This auction has already closed and is waiting on and officer "
                "to accept the results."
            ),
            Status.Stopped: (
                "This auction has been stopped and is not accepting bids at the "
                "moment."
            ),
        }
    )
    def bid(
        self, channel, bidder, bid_amount: int, bid_id: int, rank: BidderRank
    ) -> Iterable[AuctionMessage]:
        # Grab the item that is currently being bid in our channel.
        auction = typing.cast(RunningAuction, self._channels[channel])

        valid, error = validate_bid(
            bidder,
            bid_amount,
            auction.bids,
            self._dkp,
            valuable_threshold=self._limits.valuable,
            minimum=self._limits.minimum,
            maximum=self._limits.maximum,
        )
        if not valid:
            yield AuctionMessage(channel=channel, message=error, hidden=True)
            return

        # Add our bid to the system, extending the time left before the auction
        # ends if required.
        bid = Bid(bidder=bidder, bid=bid_amount, id=bid_id, rank=rank)
        auction.bids.add(bid)
        auction.last_bid = datetime.datetime.utcnow()

        yield AuctionMessage(channel=channel, message="Bid Accepted!", hidden=True)
        yield AuctionMessage(channel=channel, message=f"{bid.bidder} has bid {bid.bid}")

    @check_auction_channels
    @check_auction_status(
        {
            Status.Finished: "This auction has finished already and cannot be stopped.",
            Status.Stopped: "This auction is already stopped.",
        }
    )
    def stop(self, channel) -> Iterable[AuctionMessage]:
        auction = typing.cast(RunningAuction, self._channels[channel])
        auction.status = Status.Stopped

        yield AuctionMessage(
            channel=channel, message="Auction has been stopped", hidden=True
        )
        yield AuctionMessage(
            channel=channel,
            message=f"Auction for {auction.item.description} has been stopped.",
        )

    @check_auction_channels
    @check_auction_status(
        {
            Status.Running: "This auction has not finished and cannot be accepted yet.",
            Status.Stopped: "This auction has not finished and cannot be accepted yet.",
        }
    )
    def accept(self, channel, force=False) -> Iterable[AuctionMessage]:
        # Grab the item that is currently being bid in our channel.
        auction = typing.cast(RunningAuction, self._channels[channel])

        # We're going to compute the results again, and see if they differ, if they
        # do, we're going to refuse to accept the auction without a -force flag.
        results = determine_results(
            auction, self._dkp, member_treshold=self._limits.member
        )
        if not force and auction.results != results:
            # TODO: Mention the ability to reopen + force accept the new results.
            yield AuctionMessage(
                channel=channel,
                message=(
                    "This auction has not been accepted because the results "
                    "have changed since it closed."
                ),
                hidden=True,
            )
        else:
            # TODO: Award the item in the DKP system.
            yield AuctionMessage(
                channel=channel, message="Auction Accepted", hidden=True
            )
            yield AuctionMessage(
                channel=channel, message=f"Auction Accepted: {results}"
            )

    @check_auction_channels
    @check_auction_status(
        {
            Status.Running: "This auction has not finished and cannot be reopened yet.",
        }
    )
    def reopen(self, channel) -> Iterable[AuctionMessage]:
        # Grab the item that is currently being bid in our channel.
        auction = typing.cast(RunningAuction, self._channels[channel])

        # We're going to leave any existing bids alone, however we're going to reset the
        # auction so it runs for the full duration again, just with the bids in the same
        # state that they are now.
        auction.results = None
        auction.started_at = datetime.datetime.utcnow()
        auction.last_updated = None
        auction.last_bid = None
        auction.status = Status.Running

        yield AuctionMessage(channel=channel, message="Reopening Bidding", hidden=True)
        yield AuctionMessage(
            channel=channel,
            message=(
                f"Reopening Bids for {auction.item.description}, "
                f"ending in {humanize_delta(auction.time_left)}"
            ),
        )

    @check_auction_channels
    def restart(self, channel) -> Iterable[AuctionMessage]:
        # Grab the item that is currently being bid in our channel.
        auction = typing.cast(RunningAuction, self._channels[channel])

        # To restart the auction, we can just create a new RunningAuction with the same
        # item, and the same channel.
        item = auction.item
        auction = RunningAuction(item=item)
        self._channels[channel] = auction

        yield AuctionMessage(channel=channel, message="Restarted Auction", hidden=True)
        yield AuctionMessage(
            channel=channel,
            message=(
                f"Restarting Bids for {item.description}, "
                f"ending in {humanize_delta(auction.time_left)}"
            ),
        )

    @check_auction_channels
    def delete(self, channel) -> Iterable[AuctionMessage]:
        # Grab the item that is currently being bid in our channel.
        auction = typing.cast(RunningAuction, self._channels[channel])

        self._channels[channel] = None

        yield AuctionMessage(channel=channel, message="Auction Deleted", hidden=True)
        yield AuctionMessage(
            channel=channel,
            message=f"Auction for {auction.item.description} has been deleted.",
        )

    def next(self) -> Iterable[AuctionMessage]:
        while self._pending_items and not all(self._channels.values()):
            # If we've gotten here, then we have items to auction, and we have available
            # channels to auction them in, so let's go ahead and pick one of each.
            item = self._pending_items.pop(0)
            channel = random.choice(
                [channel for channel, item in self._channels.items() if item is None]
            )

            # We have an item and a channel, now we'll actually start the auction.
            auction = RunningAuction(item=item)
            self._channels[channel] = auction
            yield AuctionMessage(
                channel=channel,
                message=(
                    f"Starting Bid for {item.description} by {item.added_by}, "
                    f"ending in {humanize_delta(auction.time_left)}"
                ),
            )


class Role(enum.Enum):
    def __repr__(self):
        return "<%s.%s>" % (self.__class__.__name__, self.name)

    Officer = "officer"
    Raider = "raider"
    Recruit = "recruit"
    Member = "member"


def check_roles(*roles: typing.Union[Role, str, int]):
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(self, ctx: SlashContext, *args, **kwargs):
            if not set(ctx.author.roles) & {
                self.get_role(role) for role in roles if role is not None
            }:
                await ctx.send(
                    hidden=True,
                    content=(
                        "Sorry, you don't have the correct role to run this command."
                    ),
                )
            else:
                await fn(self, ctx, *args, **kwargs)

        return wrapper

    return deco


class Auction(Cog):
    def __init__(self, bot):
        self.bot = bot
        self.auctioneer = Auctioneer(
            channels=self.bot.config.auction.channels,
            limits=self.bot.config.auction.limits,
        )
        self.dkp = self.bot.get_cog("DKP")
        self.server = None
        self._role_mappings = {}
        self._run_auction.start()

    @Cog.listener(name="on_ready")
    async def _on_ready(self):
        self.server = self.bot.get_guild(self.bot.config.discord.server_id)

    def get_role(self, role: typing.Union[Role, str, int]):
        if role not in self._role_mappings:
            lookup_role = role
            if isinstance(role, Role):
                lookup_role = getattr(self.bot.config.auction.roles, role.value)

            if isinstance(lookup_role, str):
                for discord_role in self.server.roles:
                    if discord_role.name == lookup_role:
                        self._role_mappings[role] = discord_role
                        break
                else:
                    self._role_mappings[role] = None
            else:
                self._role_mappings[role] = self.server.get_role(lookup_role)

        return self._role_mappings[role]

    async def add_auction_item(self, item, quantity, added_by):
        # TODO: Fetch Item data
        # TODO: Add ACL
        self.auctioneer.add(
            AuctionItem(item=item, quantity=quantity, added_by=added_by)
        )

    @tasks.loop(seconds=5)
    async def _run_auction(self):
        # Update our DKP to catch any changes
        if self.auctioneer.has_running_auctions:
            self.auctioneer.update_dkp(await self.dkp.get_dkp())

        # Progress through any running auctions
        for message in self.auctioneer.run():
            channel = discord.utils.get(self.server.channels, name=message.channel)
            await channel.send(**message.as_kwargs())

        # Keep starting new auctions until we're not starting any more.
        for message in self.auctioneer.next():
            channel = discord.utils.get(self.server.channels, name=message.channel)
            await channel.send(**message.as_kwargs())

    @_run_auction.before_loop
    async def _before_run_auction(self):
        await self.bot.wait_until_ready()

    async def _do_bid(
        self, ctx: SlashContext, bid: int, rank: BidderRank, id_: int = 0
    ):
        self.auctioneer.update_dkp(await self.dkp.get_dkp())

        # We need to get this person's ingame character name, if they haven't linked a
        # character, then they're not allowed to bid anything.
        character = await self.dkp.get_character(ctx.author.id)
        if character is None:
            await ctx.send(
                hidden=True,
                content=(
                    "Error: You do not have a character linked to this discord "
                    "account."
                ),
            )
        else:
            for message in self.auctioneer.bid(
                ctx.channel.name, character, bid, id_, rank
            ):
                await smart_send(ctx, hidden=message.hidden, **message.as_kwargs())

    @cog_ext.cog_slash(
        name="bid",
        description="Bid on the auction",
        options=[
            create_option(
                name="bid",
                description="the amount of dkp to bid",
                option_type=OptionType.INTEGER,
                required=True,
            ),
            create_option(
                name="id",
                description="the numeric ID for the bid (default: 0)",
                option_type=OptionType.INTEGER,
                required=False,
            ),
        ],
    )
    @check_roles(Role.Officer, Role.Raider, Role.Recruit, Role.Member)
    async def _bid(self, ctx: SlashContext, bid: int, id_: int = 0):
        await ctx.defer(hidden=True)

        if self.get_role(Role.Recruit) in ctx.author.roles:
            rank = BidderRank.Recruit
        elif self.get_role(Role.Raider) in ctx.author.roles:
            rank = BidderRank.Raider
        elif self.get_role(Role.Member) in ctx.author.roles:
            rank = BidderRank.Member
        else:
            await ctx.send(
                content="Couldn't determine your bidding rank, contact an officer.",
                hidden=True,
            )
            return

        await self._do_bid(ctx, bid, rank, id_)

    @cog_ext.cog_slash(
        name="bid-alt",
        description="Bid on the auction for an alt",
        options=[
            create_option(
                name="bid",
                description="the amount of dkp to bid",
                option_type=OptionType.INTEGER,
                required=True,
            ),
            create_option(
                name="id",
                description="the numeric ID for the bid (default: 0)",
                option_type=OptionType.INTEGER,
                required=False,
            ),
        ],
    )
    @check_roles(Role.Officer, Role.Raider, Role.Recruit, Role.Member)
    async def _bidalt(self, ctx: SlashContext, bid: int, id_: int = 0):
        await ctx.defer(hidden=True)
        await self._do_bid(ctx, bid, BidderRank.Alt, id_)

    @cog_ext.cog_subcommand(
        base="auction", name="stop", description="Stop a running auction"
    )
    @check_roles(Role.Officer)
    async def _auction_stop(self, ctx: SlashContext):
        await ctx.defer(hidden=True)

        for message in self.auctioneer.stop(ctx.channel.name):
            await smart_send(ctx, hidden=message.hidden, **message.as_kwargs())

    @cog_ext.cog_subcommand(
        base="auction",
        name="accept",
        description="accept the auction results",
        options=[
            create_option(
                name="force",
                description="force accept even if results have changed (default: no)",
                option_type=OptionType.STRING,
                required=False,
                choices=["yes", "no"],
            ),
        ],
    )
    @check_roles(Role.Officer)
    async def _auction_accept(self, ctx: SlashContext, force: str = "no"):
        await ctx.defer(hidden=True)

        for message in self.auctioneer.accept(ctx.channel.name, force=force == "yes"):
            await smart_send(ctx, hidden=message.hidden, **message.as_kwargs())

    @cog_ext.cog_subcommand(
        base="auction",
        name="reopen",
        description="Reopen a stopped or finished auction for additional bids",
    )
    @check_roles(Role.Officer)
    async def _auction_reopen(self, ctx: SlashContext):
        await ctx.defer(hidden=True)

        for message in self.auctioneer.reopen(ctx.channel.name):
            await smart_send(ctx, hidden=message.hidden, **message.as_kwargs())

    @cog_ext.cog_subcommand(
        base="auction", name="delete", description="Delete an auction"
    )
    @check_roles(Role.Officer)
    async def _auction_delete(self, ctx: SlashContext):
        await ctx.defer(hidden=True)

        for message in self.auctioneer.delete(ctx.channel.name):
            await smart_send(ctx, hidden=message.hidden, **message.as_kwargs())

    @cog_ext.cog_subcommand(
        base="auction",
        name="restart",
        description=(
            "Restart an auction, invalidating all bids and starting from scratch"
        ),
    )
    @check_roles(Role.Officer)
    async def _auction_restart(self, ctx: SlashContext):
        await ctx.defer(hidden=True)

        for message in self.auctioneer.restart(ctx.channel.name):
            await smart_send(ctx, hidden=message.hidden, **message.as_kwargs())
