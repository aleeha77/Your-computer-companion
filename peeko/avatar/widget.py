"""Avatar subsystem: the on-screen robot companion.

Stage 1: the avatar is a real little character — a transparent, frameless,
always-on-top window that renders the animated robot described by
``peeko/avatar/assets/manifest.json`` and driven by the
:class:`~peeko.avatar.state_machine.AvatarStateMachine`:

* gentle idle bob (looping animation from the manifest);
* natural-ish blinking on an irregular timer;
* occasional looks left/right/up/down, plus glancing toward the mouse
  cursor when it hovers nearby;
* a happy squash-and-bounce when clicked;
* a "being carried" wiggle while dragged anywhere on the desktop.

Stage 2 adds the interaction layer:

* a subtle **hover** reaction when the pointer enters the window (it never
  interferes with a click or a drag, and ends when the pointer leaves);
* a distinct **double-click** reaction (a bigger, winkier bounce);
* the full right-click **interaction menu** — Check Status and Settings open
  real, honest windows; the planned pet actions are labelled as not
  implemented and answer with an explanation plus a "huh?" reaction;
* right-click menu / Ctrl+Q / window close quit, exactly as before.

Stage 3 connects the conversation system — while staying a desktop robot:

* the menu's **Talk** entry opens :class:`peeko.ui.chat_window.ChatWindow`,
  a separate top-level window; the robot itself stays frameless, transparent
  and always on top;
* the chat window asks this window for the structured context
  (:func:`peeko.ai.context.build_context`) and hands back the animation the
  AI reply asked for, which is played through
  :func:`peeko.avatar.expressions.apply_expression` — an unknown or
  unplayable name quietly falls back to idle;
* a small :class:`peeko.ai.context.InteractionLog` records what the user and
  Peeko just did (clicks, drags, chat turns), so the context Peeko sends is
  real data rather than invented values.

The chat window owns all AI/network work and runs it off the UI thread; this
window never waits on it. Everything animation-related still runs on
``QTimer`` callbacks — no sleeps, no blocking calls.

Stage 4 adds the robot's first **listening** behaviour, without giving this
window a microphone:

* the chat window owns the capture
  (:mod:`peeko.voice`) and tells this window when a recording starts and
  stops through
  :attr:`peeko.ui.chat_window.ChatWindow.listeningChanged`, which is
  connected to :meth:`AvatarWindow.play_listening`;
* :meth:`AvatarWindow.play_listening` plays (or ends) the sustained
  ``listening`` animation through
  :func:`peeko.avatar.expressions.apply_listening` — the same artwork-driven
  seam everything else uses;
* that animation is optional artwork: a manifest without it simply means the
  robot shows no listening pose, while the chat window keeps its own
  "listening…" indicator, so a missing animation is never a lie;
* nothing in this module opens an audio device, so the avatar still starts
  and animates on a machine with no microphone and no audio library — and
  user input still wins: the listening pose is declined while the robot is
  being dragged.

Stage 5 adds the matching **talking** behaviour, again without this window
owning any audio: the chat window speaks Peeko's reply off the UI thread and
tells this window through
:attr:`peeko.ui.chat_window.ChatWindow.speakingChanged`, which is connected to
:meth:`AvatarWindow.play_speaking` and
:func:`peeko.avatar.expressions.apply_speaking`. It is optional artwork too,
yields to a drag, and — when a capture is still open — talking wins while the
playback lasts, exactly as the state machine documents. This window still
never opens an audio device or a network connection.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCursor,
    QKeySequence,
    QPainter,
    QShortcut,
)
from PySide6.QtWidgets import QWidget

from peeko.ai.context import InteractionLog, build_context
from peeko.avatar.assets import AssetLibrary
from peeko.avatar.expressions import (
    apply_emotion,
    apply_expression,
    apply_listening,
    apply_sleeping,
    apply_speaking,
    apply_yawn,
)
from peeko.avatar.manifest import load_manifest
from peeko.avatar.renderer import draw_frame
from peeko.avatar.state_machine import IDLE, AvatarStateMachine
from peeko.emotions.engine import EmotionEngine, FeedResult, SleepResult
from peeko.needs.behaviour import EVENT_ATTENTION, EVENT_AUTO_SLEEP
from peeko.needs.behaviour import EVENT_AUTO_WAKE, EVENT_YAW
from peeko.ui.chat_window import ChatWindow
from peeko.ui.context_menu import (
    EMOTION_ACTION_IDS,
    FEED_ID,
    NEEDS_ACTION_IDS,
    SLEEP_ID,
    WAKE_ID,
    QUIT_ID,
    SETTINGS_ID,
    STATUS_ID,
    TALK_ID,
    build_avatar_context_menu,
    find_entry,
)
from peeko.ui.dialogs import (
    show_not_implemented_dialog,
    show_notice_dialog,
    show_settings_dialog,
    show_status_dialog,
)
from peeko.ui.context_menu import (
    NEEDS_BARS_ID,
    build_needs_bars_action,
)
from peeko.ui.food_picker import FoodPickerDialog, show_food_picker
from peeko.ui.needs_bars import NeedsBarsPanel

LOG = logging.getLogger("peeko.avatar")

#: Where the packaged placeholder artwork lives (next to this module).
DEFAULT_ASSETS_DIR = Path(__file__).resolve().parent / "assets"

#: Animation tick interval (ms) — 30 fps is plenty for a tiny character.
TICK_INTERVAL_MS = 33
#: How often we check where the mouse cursor is (for glances).
POINTER_POLL_MS = 150
#: Pointer distance from the window centre that triggers a glance (px).
POINTER_GLANCE_RADIUS = 320
#: Mouse movement (px) that separates a click from a drag.
DRAG_THRESHOLD_PX = 6
#: How often the emotion engine's drift is advanced (ms). Once a second is
#: far finer than the simulated decay (per *hour*), and the tick is a few
#: float operations — it can never block the UI thread.
EMOTION_TICK_MS = 1000


class AvatarWindow(QWidget):
    """Frameless, translucent, always-on-top window showing the avatar.

    Signals:
        quitRequested: emitted when the user asks Peeko to quit (menu,
            Ctrl+Q, or window-close equivalent).
    """

    quitRequested = Signal()
    #: Emitted when a menu entry that is not implemented yet is chosen
    #: (carries the entry id) — useful for logging and tests.
    notImplementedRequested = Signal(str)

    #: Stage 6: emitted after a pet/play interaction was applied by the
    #: emotion engine (carries the interaction id) — handy for logging/tests.
    interactionApplied = Signal(str)

    #: Stage 7: emitted when the needs system did something on its own —
    #: ``yawn``, ``auto_sleep``, ``auto_wake`` or ``attention`` (the boredom
    #: nudge). Rate-limited in ``peeko.needs.behaviour``.
    needsEvent = Signal(str)

    def __init__(self, settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._drag_offset: QPoint | None = None
        self._press_global: QPoint | None = None
        self._press_active = False
        self._drag_active = False
        self._last_frame_key: tuple | None = None
        self._dialogs: list = []
        self._settings_dialog = None
        self._chat_window: ChatWindow | None = None
        #: Real, bounded record of what the user and Peeko just did — sent to
        #: the AI as part of the structured context (Stage 3).
        self._interactions = InteractionLog()
        #: Stage 6: Peeko's live mood and needs. One engine per window; it
        #: drifts on the timer below, changes when the user interacts, and is
        #: what the chat context and Check Status read.
        self._emotions = EmotionEngine()

        # ---- asset pipeline: manifest -> pixmaps -> machine ---------------- #
        # ``PEEKO_AVATAR_ASSETS_DIR`` lets the owner point Peeko at their own
        # artwork folder; empty means "use the artwork shipped in the package".
        assets_dir = Path(
            getattr(settings, "avatar_assets_dir", None) or DEFAULT_ASSETS_DIR
        )
        self._manifest = load_manifest(assets_dir / "manifest.json")
        self._assets = AssetLibrary(self._manifest)
        self._assets.load()
        self._machine = AvatarStateMachine(self._manifest, rng=random.Random())
        LOG.info(
            "Avatar ready: %d animation(s), %d layer(s), canvas %dx%d.",
            len(self._manifest.animations),
            len(self._manifest.z_order),
            self._manifest.canvas_width,
            self._manifest.canvas_height,
        )

        # ---- window -------------- --------------------------------------- #
        self.setWindowTitle("Peeko")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # keep out of the taskbar/dock
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFixedSize(
            self._manifest.canvas_width, self._manifest.canvas_height
        )

        # ---- Stage 7: the visible needs bars (attached to the robot) ------ #
        # ON by default — the owner asked to *see* the stats — and toggled
        # from the right-click menu's checkable "Show Needs Bars" entry. The
        # panel is a child *window* of this one, so it always travels with the
        # robot and is never a separate desktop window of its own.
        self._needs_bars_visible = bool(
            getattr(settings, "show_needs_bars", True)
        )
        self._needs_panel: NeedsBarsPanel | None = None

        self._menu = build_avatar_context_menu(self)
        self._needs_bars_action = build_needs_bars_action(
            self._menu, checked=self._needs_bars_visible
        )
        # Directly above the separator before Quit: a real toggle rather than
        # one of the pet actions, so it is not part of MENU_SPEC.
        self._menu.insertAction(
            self._menu.actions()[-2], self._needs_bars_action
        )
        self._menu.triggered.connect(self._on_menu_triggered)

        quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        quit_shortcut.activated.connect(self.quitRequested.emit)
        self.quitRequested.connect(self._quit)

        self._place_on_screen()
        self._show_needs_bars(self._needs_bars_visible)

        # ---- animation loop (QTimer-driven; never blocks) ------------------ #
        self._elapsed = QElapsedTimer()
        self._elapsed.start()
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(TICK_INTERVAL_MS)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()

        self._pointer_timer = QTimer(self)
        self._pointer_timer.setInterval(POINTER_POLL_MS)
        self._pointer_timer.timeout.connect(self._on_pointer_poll)
        self._pointer_timer.start()

        # ---- emotion drift (Stage 6, QTimer-driven, never blocks) ---------- #
        self._emotion_timer = QTimer(self)
        self._emotion_timer.setInterval(EMOTION_TICK_MS)
        self._emotion_timer.timeout.connect(self._on_emotion_tick)
        self._emotion_timer.start()

    # ------------------------------------------------------------------ #
    # Geometry
    # ------------------------------------------------------------------ #
    def _place_on_screen(self) -> None:
        """Position the window on the primary screen (top-right, inset)."""
        screen = self.screen() or self.windowHandle()
        if screen is None:  # pragma: no cover - defensive, no display yet
            return
        available = screen.availableGeometry()
        margin = 24
        self.move(available.right() - self.width() - margin,
                  available.top() + margin)

    # ------------------------------------------------------------------ #
    # Animation loop
    # ------------------------------------------------------------------ #
    def _on_tick(self) -> None:
        """One animation tick: advance the state machine, repaint if needed."""
        dt_ms = self._elapsed.restart()
        self._machine.tick(dt_ms)
        key = (self._machine.state, self._machine.frame_index)
        if key != self._last_frame_key:
            self._last_frame_key = key
            self.update()

    def _on_pointer_poll(self) -> None:
        """Cheap "taste" of cursor awareness: glance toward a nearby cursor."""
        if self._press_active or self._machine.state != IDLE:
            return
        cursor = QCursor.pos()
        center = self.frameGeometry().center()
        delta = cursor - center
        if abs(delta.x()) + abs(delta.y()) > POINTER_GLANCE_RADIUS:
            return
        if abs(delta.x()) > abs(delta.y()):
            direction = "left" if delta.x() < 0 else "right"
        else:
            direction = "up" if delta.y() < 0 else "down"
        self._machine.pointer_direction(direction)

    # ------------------------------------------------------------------ #
    # Painting
    # ------------------------------------------------------------------ #
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        """Paint the current animation frame from the asset manifest."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        draw_frame(painter, self._assets, self._machine.frame)
        painter.end()

    # ------------------------------------------------------------------ #
    # Mouse interaction (hover / click / double-click vs drag)
    # ------------------------------------------------------------------ #
    def enterEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        """Pointer entered the avatar -> a subtle "oh!" reaction.

        The state machine decides whether it is a good moment (it stays
        quiet while a press/drag is in flight), so this never interferes
        with clicking or dragging.
        """
        if self._machine.hover_enter():
            LOG.debug("Hover reaction triggered.")

    def leaveEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        """Pointer left the avatar -> end a running hover reaction at once."""
        if self._machine.hover_leave():
            LOG.debug("Hover reaction cut short by pointer leaving.")

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton:
            self._arm_press(event.globalPosition().toPoint())
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """A double-click plays its own, more excited reaction.

        Qt delivers press -> release (which already fired the single-click
        reaction) -> double-click -> release. Re-arming the press here keeps
        "double-click and then drag" working, and the state machine refuses
        to downgrade the double-click reaction to a plain click when the
        following release arrives.
        """
        if event.button() != Qt.LeftButton:
            return
        self._arm_press(event.globalPosition().toPoint())
        if self._machine.double_click():
            LOG.debug("Double-click reaction triggered.")
        self._interactions.record("user double-clicked the robot")
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if not (self._press_active and event.buttons() & Qt.LeftButton):
            return
        if not self._drag_active:
            delta = event.globalPosition().toPoint() - self._press_global
            if delta.manhattanLength() > DRAG_THRESHOLD_PX:
                self._drag_active = True
                self._machine.drag_started()
        if self._drag_active:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton and self._press_active:
            self._press_active = False
            self._machine.release(moved=self._drag_active)
            self._interactions.record(
                "user dragged the robot around"
                if self._drag_active else "user clicked the robot"
            )
            self._drag_offset = None
            self._press_global = None
            self._drag_active = False
            event.accept()

    def _arm_press(self, global_pos: QPoint) -> None:
        """Remember where a left-button interaction started (click or drag)."""
        self._press_active = True
        self._drag_active = False
        self._press_global = global_pos
        self._drag_offset = global_pos - self.frameGeometry().topLeft()
        self._machine.press()

    # ------------------------------------------------------------------ #
    # Interaction menu / dialogs / quit
    # ------------------------------------------------------------------ #
    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._menu.exec(event.globalPos())

    def _on_menu_triggered(self, action) -> None:
        """Dispatch a menu entry by its stable id (see ``ui.context_menu``)."""
        action_id = action.data()
        entry = find_entry(action_id)
        if action_id == QUIT_ID:
            self.quitRequested.emit()
        elif action_id == TALK_ID:
            self._show_chat()
        elif action_id == STATUS_ID:
            self._show_status()
        elif action_id == SETTINGS_ID:
            self._show_settings()
        elif action_id in EMOTION_ACTION_IDS:
            # Stage 6: Pet and Play are real — they change Peeko's mood and
            # needs through the emotion engine.
            self._on_pet_action(action_id)
        elif action_id == FEED_ID:
            # Stage 7: Feed offers the real food catalogue.
            self.show_food_picker()
        elif action_id == NEEDS_BARS_ID:
            # Stage 7: the checkable "Show Needs Bars" toggle (default ON).
            # Qt flips the action's checked state before emitting, so a real
            # click and a direct call both end up honest: follow Qt when it
            # disagrees with what is on screen, otherwise flip the bars.
            checked = getattr(action, "isChecked", None)
            if callable(checked) and bool(checked()) != self._needs_bars_visible:
                self.set_needs_bars_visible(bool(checked()))
            else:
                self.toggle_needs_bars()
        elif action_id in NEEDS_ACTION_IDS:
            # Stage 7: Sleep / Wake Up drive the real sleep state.
            if action_id == SLEEP_ID:
                self.sleep()
            else:
                self.wake()
        elif entry is not None and not entry.implemented:
            self._on_not_implemented(entry)
        else:  # pragma: no cover - defensive: unknown action id
            LOG.warning("Unknown menu action id: %r", action_id)

    def _show_status(self) -> None:
        """Check Status: an honest readout of live state.

        Stage 6: the readout includes the six live stats, and checking on
        Peeko counts as the small documented "status" interaction, so the
        numbers in the dialog are the state *after* the tick that freshened
        them.
        """
        LOG.info("Showing status readout.")
        self._emotions.tick()
        self._emotions.interact("status")
        self._keep_dialog(
            show_status_dialog(
                self, self._settings, machine=self._machine,
                manifest=self._manifest, emotions=self._emotions,
            )
        )

    def _show_chat(self) -> None:
        """Talk: open (or re-focus) the Stage 3 chat window.

        The chat is a separate top-level window on purpose — the robot stays
        a floating desktop character, and the conversation gets a normal
        window with a title bar. The same window is reused, so the
        conversation is not lost by accident.
        """
        if self._chat_window is None:
            self._chat_window = ChatWindow(
                self._settings,
                context_provider=self.build_ai_context,
                interactions=self._interactions,
                parent=self,
            )
            self._chat_window.expressionRequested.connect(
                self._on_expression_requested
            )
            # Stage 4: the chat window owns the microphone; the robot only
            # shows what it is doing ("listening…").
            self._chat_window.listeningChanged.connect(self.play_listening)
            # Stage 5: the chat window owns playback too; the robot only shows
            # that it is speaking.
            self._chat_window.speakingChanged.connect(self.play_speaking)
            LOG.info("Chat window created.")
        self._interactions.record("user opened the chat window")
        self._chat_window.show()
        self._chat_window.raise_()
        self._chat_window.activateWindow()

    # ------------------------------------------------------------------ #
    # The Stage 3 seam: context out, expressions in
    # ------------------------------------------------------------------ #
    def build_ai_context(self):
        """Structured context for the next chat message.

        Since Stage 6 the mood (``emotion``, ``happiness``) and the needs
        (``energy``, ``hunger``, ``sleepiness``, ``friendship``) are the live
        values of :attr:`emotions` — Peeko's real, drifting state, not
        placeholders. Memory (Stage 8) and app awareness (Stage 9) are still
        honestly reported as unknown. The drift is advanced first so the model
        sees the mood as of the message. Nothing in this method touches the
        network or blocks.
        """
        self._emotions.tick()
        return build_context(
            emotional_state=self._emotions.state,
            needs=self._emotions.needs,
            interactions=self._interactions,
        )

    # ------------------------------------------------------------------ #
    # Stage 6: mood and needs
    # ------------------------------------------------------------------ #
    @property
    def emotions(self) -> EmotionEngine:
        """The live emotion engine (mood, needs, interaction effects)."""
        return self._emotions

    def _on_emotion_tick(self) -> None:
        """Advance the mood/needs drift by however long really passed.

        Stage 7: the tick also asks the needs system what Peeko did on his own
        (yawned, fell asleep, woke up, asked for attention) and shows it. The
        drift is skipped while the user is dragging the robot: they are playing
        with it, so it is not idling.
        """
        idle = not (self._drag_active or self._machine.is_pressed)
        hours = self._emotions.tick(idle=idle)
        if hours:
            LOG.debug("Emotion drift %.4fh -> %s", hours,
                      self._emotions.describe())
        self._apply_needs_events()
        self._refresh_needs_bars()

    # ------------------------------------------------------------------ #
    # Stage 7: feeding, sleeping and needs-driven behaviour
    # ------------------------------------------------------------------ #
    def feed(self, food_id: str) -> FeedResult:
        """Feed Peeko one food through the emotion engine.

        The food's documented deltas are applied to the live needs and mood,
        the avatar reacts with an existing animation for the resulting mood,
        and a refusal (asleep, or already full) is reported as plainly as it is
        decided — with no food consumed.
        """
        self._emotions.tick()
        result = self._emotions.feed(food_id)
        if not result.applied:
            self._interactions.record(
                f"user offered {result.food.label}: refused ({result.message})"
            )
            LOG.info("Feed refused: %s", result.message)
            self._keep_dialog(
                show_notice_dialog(
                    self, f"Feed — {result.food.label}", result.message,
                    action="the offered food",
                )
            )
            return result
        self._interactions.record(f"user fed Peeko: {result.message}")
        apply_emotion(self._machine, self._emotions.mood_signal().emotion)
        LOG.info("Fed %s -> %s", result.food.label, self._emotions.describe())
        self.interactionApplied.emit(f"feed_{result.food.id}")
        self._apply_needs_events()
        return result

    def show_food_picker(self) -> FoodPickerDialog:
        """Open the honest Feed picker for the current state."""
        dialog = show_food_picker(
            self, self.feed,
            asleep=self._emotions.is_asleep,
            full=self._emotions.needs.is_full(),
        )
        self._keep_dialog(dialog)
        return dialog

    def sleep(self) -> SleepResult:
        """Deliberate nap (the Sleep menu entry)."""
        result = self._emotions.sleep()
        self._interactions.record(f"user chose Sleep: {result.message}")
        if not result.changed:
            self._keep_dialog(
                show_notice_dialog(
                    self, "Sleep", result.message, action="the Sleep request"
                )
            )
            return result
        apply_sleeping(self._machine, True)
        LOG.info("Asleep -> %s", self._emotions.describe())
        self.interactionApplied.emit("sleep")
        return result

    def wake(self) -> SleepResult:
        """End the nap (the Wake Up menu entry)."""
        result = self._emotions.wake()
        self._interactions.record(f"user chose Wake Up: {result.message}")
        if not result.changed:
            self._keep_dialog(
                show_notice_dialog(
                    self, "Wake Up", result.message,
                    action="the Wake Up request",
                )
            )
            return result
        apply_sleeping(self._machine, False)
        LOG.info("Awake -> %s", self._emotions.describe())
        self.interactionApplied.emit("wake")
        return result

    def _apply_needs_events(self) -> None:
        """Show whatever the needs system decided on its own (never spams).

        Yawning shows the yawn reaction when the artwork has one and the
        documented tired blink otherwise; falling asleep and waking up drive
        the sleeping pose; the boredom nudge plays one gentle existing
        animation and is recorded in the interaction log. Every limit lives in
        :mod:`peeko.needs.behaviour`, not here.
        """
        for event in self._emotions.take_events():
            self._interactions.record(event.summary)
            if event.kind == EVENT_YAW:
                played = apply_yawn(self._machine)
                LOG.info("Needs: yawn (avatar state %r)", played)
            elif event.kind == EVENT_AUTO_SLEEP:
                apply_sleeping(self._machine, True)
                LOG.info("Needs: Peeko fell asleep")
            elif event.kind == EVENT_AUTO_WAKE:
                apply_sleeping(self._machine, False)
                LOG.info("Needs: Peeko woke up")
            elif event.kind == EVENT_ATTENTION:
                # The closest thing the placeholder artwork has to
                # "hey, look at me" — an existing animation, never a fake one.
                apply_expression(self._machine, "talking_curious")
                LOG.info("Needs: boredom nudge")
            self.needsEvent.emit(event.kind)

    def _on_pet_action(self, action_id: str) -> None:
        """Pet / Play: apply the engine's documented interaction effect.

        The mood change is real and visible (Check Status shows it), and the
        dominant emotion it produces cues one of the *existing* animations
        through :func:`peeko.avatar.expressions.apply_emotion` — with the
        documented fallback when the placeholder artwork cannot play it.
        """
        self._emotions.tick()  # freshen before applying the delta
        effect = self._emotions.interact(action_id)
        self._interactions.record(f"user chose {effect.id}: {effect.summary}")
        played = apply_emotion(self._machine, self._emotions.dominant_emotion())
        LOG.info("Interaction %r -> %s (avatar state %r)",
                 effect.id, self._emotions.describe(), played)
        self.interactionApplied.emit(effect.id)

    def _on_expression_requested(self, animation: str) -> str | None:
        """One AI reply arrived: count the chat turn, then play its animation.

        A reply means a conversation turn really happened, so the engine's
        documented "talk" effect applies (Peeko gets a little happier and
        friendlier). Then the reply's animation is played exactly as before.
        """
        effect = self._emotions.chat_turn()
        self._interactions.record(f"chat turn: {effect.summary}")
        return self.play_expression(animation)

    def play_expression(self, animation: str) -> str | None:
        """Play the animation the AI reply asked for.

        The name is mapped onto one of the animations the *current* artwork
        manifest really has (see :mod:`peeko.avatar.expressions`); an unknown
        name, or one this manifest cannot play, quietly falls back to idle.
        User input wins: while the robot is being dragged or held nothing is
        interrupted.

        :returns: the avatar state played, or ``None`` when the cue was
            declined.
        """
        played = apply_expression(self._machine, animation)
        LOG.info("AI expression %r -> avatar state %r", animation, played)
        return played

    def play_listening(self, listening: bool) -> str | None:
        """Show or end the listening animation while a voice capture runs.

        Stage 4: the chat window starts/stops the microphone and tells this
        window through :attr:`peeko.ui.chat_window.ChatWindow.listeningChanged`.
        The animation comes from the *same* artwork manifest as everything
        else (:mod:`peeko.avatar.expressions`), and is optional: artwork
        without a ``listening`` animation simply shows nothing, while the chat
        window keeps its own "listening…" indicator.

        :returns: the avatar state played, or ``None`` when nothing changed.
        """
        played = apply_listening(self._machine, bool(listening))
        LOG.info("Voice %s -> avatar state %r",
                 "listening" if listening else "stopped listening", played)
        return played

    def play_speaking(self, speaking: bool) -> str | None:
        """Show or end the talking animation while Peeko's reply is played.

        Stage 5: the chat window synthesizes and plays the reply on a worker
        thread and tells this window through
        :attr:`peeko.ui.chat_window.ChatWindow.speakingChanged`. Like the
        listening pose this animation is optional artwork, and like every
        other cue it is declined while the user is dragging the robot — the
        chat window's own "Peeko is speaking…" indicator is what keeps the
        report truthful either way.

        :returns: the avatar state played, or ``None`` when nothing changed.
        """
        played = apply_speaking(self._machine, bool(speaking))
        LOG.info("Speech %s -> avatar state %r",
                 "started" if speaking else "finished", played)
        return played

    # ------------------------------------------------------------------ #
    # Stage 7: the visible needs bars (part of the robot, not a window)
    # ------------------------------------------------------------------ #
    def _ensure_needs_panel(self) -> NeedsBarsPanel:
        """The panel, created on first use (never shown until asked)."""
        if self._needs_panel is None:
            self._needs_panel = NeedsBarsPanel(self)
        return self._needs_panel

    def _show_needs_bars(self, visible: bool) -> None:
        """Show or hide the bars, keeping the menu toggle in step."""
        self._needs_bars_visible = bool(visible)
        panel = self._ensure_needs_panel()
        if self._needs_bars_visible:
            panel.follow(self)
            panel.show()
        else:
            panel.hide()
        action = getattr(self, "_needs_bars_action", None)
        if action is not None:
            blocked = action.blockSignals(True)
            action.setChecked(self._needs_bars_visible)
            action.blockSignals(blocked)

    def _refresh_needs_bars(self) -> None:
        """Push the live engine snapshot into the bars (real time, never blocks).

        The snapshot is the same one Check Status and the AI context use, so a
        bar can never disagree with the simulation the user is watching.
        """
        if not self._needs_bars_visible:
            return
        panel = self._ensure_needs_panel()
        panel.update_from_snapshot(self._emotions.snapshot())
        panel.follow(self)
        if not panel.isVisible():
            panel.show()

    @property
    def needs_bars_visible(self) -> bool:
        """Are the needs bars currently shown (default: yes)?"""
        return bool(self._needs_bars_visible)

    @property
    def needs_panel(self) -> NeedsBarsPanel:
        """The attached needs-bars panel (created on first use)."""
        return self._ensure_needs_panel()

    def set_needs_bars_visible(self, visible: bool) -> None:
        """Show or hide the bars (the "Show Needs Bars" menu toggle)."""
        LOG.info("Needs bars %s.", "shown" if visible else "hidden")
        self._show_needs_bars(visible)

    def needs_bars_action(self):
        """The checkable menu action that shows/hides the bars."""
        return self._needs_bars_action

    def toggle_needs_bars(self) -> bool:
        """Flip the bars and return the new visibility."""
        self.set_needs_bars_visible(not self._needs_bars_visible)
        return self._needs_bars_visible

    def _show_settings(self) -> None:
        """Settings: the (read-only) configuration Peeko is running with."""
        LOG.info("Showing settings window.")
        if self._settings_dialog is None:
            self._settings_dialog = show_settings_dialog(self, self._settings)
        else:
            self._settings_dialog.show()
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()

    def _on_not_implemented(self, entry) -> None:
        """A planned action: react honestly instead of pretending."""
        LOG.info("Menu entry %r is not implemented (Stage %s).",
                 entry.id, entry.stage)
        self._machine.confused()
        self.notImplementedRequested.emit(entry.id)
        self._keep_dialog(show_not_implemented_dialog(self, entry))

    def _keep_dialog(self, dialog) -> None:
        """Hold a reference so an async dialog isn't collected mid-display."""
        alive = []
        for existing in self._dialogs:
            try:
                if existing.isVisible():
                    alive.append(existing)
            except RuntimeError:  # pragma: no cover - already deleted by Qt
                continue
        alive.append(dialog)
        self._dialogs = alive

    def _quit(self) -> None:
        LOG.info("Quit requested — closing avatar window.")
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        LOG.info("Avatar window closed.")
        super().closeEvent(event)