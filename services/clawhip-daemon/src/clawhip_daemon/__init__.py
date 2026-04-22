"""clawhip-daemon — supervises the vendored clawhip subprocess and owns outbound sink rendering.

Scope boundary: clawhip-daemon handles delivery out to Telegram (and future
sinks); telegram-gateway handles inbound Telegram commands. Together they
bracket the operator's Telegram experience.

Story 1.2 ships only `__version__`. Real logic arrives in: Story 7.8
(proactive self-recovered summary in telegram-sink); larger build-out via
clawhip vendoring (Story 1.3).
"""

__version__ = "0.1.0"
