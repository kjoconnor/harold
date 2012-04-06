#!/usr/bin/python

import traceback

from twisted.words.protocols import irc
from twisted.internet import protocol, ssl
from twisted.application import internet
from twisted.web import resource

from dispatcher import Dispatcher
from http import ProtectedResource
from plugin import Plugin
from conf import PluginConfig, Option, tup


class IrcConfig(PluginConfig):
    nick = Option(str)
    password = Option(str, default=None)
    host = Option(str)
    port = Option(int, default=6667)
    use_ssl = Option(bool, default=False)
    channels = Option(tup, default=[])


def git_commit_id():
    from subprocess import Popen, PIPE

    try:
        result = Popen(["git", "rev-parse", "HEAD"], stdout=PIPE)
        return result.communicate()[0][:8]
    except:
        return ""

REVISION = git_commit_id()


def version(irc, sender, channel):
    nick = sender.partition('!')[0]
    irc.send_message(channel, "%s, i am running git revision %s" % (nick,
                                                                    REVISION))


def who(irc, sender, channel):
    irc.me(channel, "is a bot. see http://github.com/spladug/harold")


class IRCBot(irc.IRCClient):
    realname = "Harold (%s)" % REVISION
    lineRate = .25  # rate limit to 4 messages / second

    def signedOn(self):
        self.topics = {}
        self.topic_i_just_set = None

        for channel in self.factory.channels:
            self.join(channel)

        self.factory.dispatcher.registerConsumer(self)

    def topicUpdated(self, user, channel, topic):
        if topic != self.topic_i_just_set:
            self.topics[channel] = topic

    def connectionLost(self, reason):
        irc.IRCClient.connectionLost(self)
        self.factory.dispatcher.deregisterConsumer(self)

    def privmsg(self, user, channel, msg):
        split = msg.split()

        if len(split) < 2:
            return

        highlight, command, args = split[0].lower(), split[1].lower(), split[2:]

        if not highlight.startswith(self.nickname):
            return

        fn = self.plugin.commands.get(command)
        if not fn:
            self.me(channel, "is just a simple bot. he has no idea what you mean!")
            return

        try:
            fn(self, user, channel, *args)
        except:
            traceback.print_exc()
            self.me(channel, "just had a hiccup.")

    def send_message(self, channel, message):
        self.msg(channel, message.encode('utf-8'))

    def set_topic(self, channel, topic):
        self.topic_i_just_set = topic
        self.send_message("ChanServ", " ".join(("TOPIC", channel, topic)))

    def restore_topic(self, channel):
        self.set_topic(channel, self.topics[channel])


class IRCBotFactory(protocol.ClientFactory):
    def __init__(self, plugin, config, dispatcher, channels):
        self.plugin = plugin
        self.config = config
        self.dispatcher = dispatcher
        self.channels = channels

        class _ConfiguredBot(IRCBot):
            nickname = self.config.nick
            password = self.config.password
            plugin = self.plugin
        self.protocol = _ConfiguredBot

    def clientConnectionLost(self, connector, reason):
        connector.connect()


class MessageListener(ProtectedResource):
    isLeaf = True

    def __init__(self, http, dispatcher):
        ProtectedResource.__init__(self, http)
        self.dispatcher = dispatcher

    def _handle_request(self, request):
        channel = request.args['channel'][0]
        message = request.args['message'][0]
        self.dispatcher.send_message(channel, message)


class SetTopicListener(ProtectedResource):
    isLeaf = True

    def __init__(self, http, dispatcher):
        ProtectedResource.__init__(self, http)
        self.dispatcher = dispatcher

    def _handle_request(self, request):
        channel = request.args['channel'][0]
        new_topic = request.args['topic'][0]
        self.dispatcher.set_topic(channel, new_topic)


class RestoreTopicListener(ProtectedResource):
    isLeaf = True

    def __init__(self, http, dispatcher):
        ProtectedResource.__init__(self, http)
        self.dispatcher = dispatcher

    def _handle_request(self, request):
        channel = request.args['channel'][0]
        self.dispatcher.restore_topic(channel)


class ChannelManager(object):
    def __init__(self, basic_channels, bot):
        self.bot = bot
        self.channels = set(basic_channels)

    def add(self, channel):
        if channel not in self.channels:
            self.channels.add(channel)
            self.bot.join(channel)

    def __iter__(self):
        return self.channels.__iter__()


class IrcPlugin(Plugin):
    def __init__(self):
        self.commands = {}
        super(IrcPlugin, self).__init__()

    def register_command(self, handler):
        self.commands[handler.__name__] = handler


def make_plugin(config, http):
    irc_config = IrcConfig(config)
    dispatcher = Dispatcher()
    channel_manager = ChannelManager(irc_config.channels, dispatcher)

    # add the http resources
    http.root.putChild('message', MessageListener(http, dispatcher))
    topic_root = resource.Resource()
    http.root.putChild('topic', topic_root)
    topic_root.putChild('set', SetTopicListener(http, dispatcher))
    topic_root.putChild('restore', RestoreTopicListener(http, dispatcher))

    # configure the default irc commands
    p = IrcPlugin()
    p.register_command(version)
    p.register_command(who)

    # set up the IRC client
    irc_factory = IRCBotFactory(p, irc_config, dispatcher, channel_manager)
    p.bot = dispatcher
    p.channels = channel_manager
    if irc_config:
        context_factory = ssl.ClientContextFactory()
        p.add_service(internet.SSLClient(irc_config.host,
                                         irc_config.port,
                                         irc_factory,
                                         context_factory))
    else:
        p.add_service(internet.TCPClient(irc_config.host,
                                         irc_config.port,
                                         irc_factory))
    return p
