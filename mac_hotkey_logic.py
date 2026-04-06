from dataclasses import dataclass


@dataclass
class HotkeySuppressState:
    quote_latched: bool = False
    last_suppress_ts: float = 0.0


@dataclass
class HotkeySuppressDecision:
    suppress: bool
    action: str
    state: HotkeySuppressState


def evaluate_hotkey_suppression(
    *,
    event_type: str,
    keycode: int,
    hotkey_keycode: int,
    modifier_flag_active: bool,
    modifier_pressed: bool,
    combo_active: bool,
    state: HotkeySuppressState,
    now: float,
) -> HotkeySuppressDecision:
    """Decide whether to suppress a hotkey key event on macOS.

    The key point is latching the non-modifier hotkey key once it participates
    in the combo. That way, repeated keydown events and the final keyup are
    still suppressed even if the modifier is released first.
    """
    if keycode != hotkey_keycode:
        return HotkeySuppressDecision(False, "not_hotkey_key", state)

    context_active = (
        modifier_flag_active
        or modifier_pressed
        or combo_active
        or state.quote_latched
    )

    if event_type == "key_down":
        if context_active:
            return HotkeySuppressDecision(
                True,
                "suppress_hotkey_down",
                HotkeySuppressState(True, now),
            )
        return HotkeySuppressDecision(False, "pass_through", state)

    if event_type == "key_up":
        if state.quote_latched:
            return HotkeySuppressDecision(
                True,
                "suppress_latched_keyup",
                HotkeySuppressState(False, now),
            )
        if modifier_flag_active or modifier_pressed or combo_active:
            return HotkeySuppressDecision(
                True,
                "suppress_hotkey_keyup",
                HotkeySuppressState(False, now),
            )
        return HotkeySuppressDecision(False, "pass_through", state)

    return HotkeySuppressDecision(False, "pass_through", state)
