"""Qt-side model for the settings window — the only thing standing between QML and the two
places a setting can live: the config file (shared/settings.py) and the OS credential store.

Deliberately thin, like model.py: no rule about what a pane contains lives here. Panes, rows,
labels, defaults and provider capabilities all come from `shared/schemas/settings.json`, so this
file exposes the schema rather than restating it (hard rule 3).

Secrets never touch the settings file. `keyState`/`setKey` talk to `keyring` under service
`not-hal`, keyed by PROVIDER (spec/50 rule 10) — the same entries claude.py and the Groq cleanup
already read.
"""
from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Property, QObject, Signal, Slot

from shared import settings
from backend.llm import providers

log = logging.getLogger("nothal.teleprompter")

from shared.config import KEY_SERVICE

# The ONLY fields the UI may persist into a provider's `models[<pid>]` entry. An allowlist, not a
# comment: `addProvider`/`setModel` merge whatever the form hands them, so without this a field
# named `key`/`api_key` would land in settings.json — the one file spec/50 rule 10 says a secret
# must never enter. These are exactly what the router reads back (backend/router.py).
_PERSIST_FIELDS = frozenset(
    {"on", "model", "effort", "thinking", "temperature", "endpoint", "keep_alive"})


class SettingsModel(QObject):
    """One `changed` signal for the lot — the window is small and rebuilt on open, so
    re-evaluating its bindings per write costs nothing worth plumbing around."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # The live model lists, per provider, filled by refreshModels() off a worker thread.
        # Only ever holds a non-empty result: a failed fetch leaves the offline fallback in play
        # rather than blanking a picker the user was already reading.
        self._live: dict[str, list[str]] = {}
        self._fetching: set[str] = set()
        # The last probe outcome per provider: ok · nokey · auth · unreachable · empty · error
        # (backend/llm/providers.probe). Absent until something has actually asked.
        self._status: dict[str, str] = {}
        # Per-provider probe generation. A forced probe can overtake one already in flight (that
        # is what Test means), so a returning worker must prove it is still the newest before it
        # writes — otherwise the stale answer lands last and the user reads the wrong status.
        self._gen: dict[str, int] = {}
        # The TRIAL: one slot, not a per-provider cache. "Does this key I just typed work?" is a
        # question about an unsaved credential, and answering it into the shared provider cache is
        # what let a stored key's result be read as a verdict on a typed one (Thomas, 2026-08-01).
        # The roster keeps using `_live`/`_status`; the Add/Edit sheet reads only this.
        self._trial: dict = {"pid": "", "status": "", "models": []}
        self._trial_gen = 0
        self._lock = threading.Lock()

    # --- the schema: what to draw ------------------------------------------------

    @Property("QVariant", constant=True)
    def panes(self) -> list:
        return settings.schema()["panes"]

    @Property("QVariant", constant=True)
    def meta(self) -> dict:
        """Every setting's declaration, keyed by id (type, default, pane, label, help, built)."""
        return settings.schema()["settings"]

    @Property("QVariant", constant=True)
    def catalog(self) -> dict:
        """The providers Manage can offer."""
        return settings.schema()["providers"]

    # --- the values: what is set --------------------------------------------------

    @Property("QVariant", notify=changed)
    def values(self) -> dict:
        return settings.load()

    @Slot(str, "QVariant")
    def set(self, key: str, value) -> None:
        # A QML object/array literal crosses as a QJSValue, not a dict/list — json.dumps then
        # raises inside settings.set and the write vanishes with no signal (the class that ate the
        # Add form). Unwrap before it reaches the file. A scalar has no toVariant and passes through.
        if value is not None and hasattr(value, "toVariant"):
            value = value.toVariant()
        settings.set(key, value)
        self.changed.emit()

    @Slot(str, result=bool)
    def validateBinding(self, combo: str) -> bool:
        """Is `combo` a shortcut the daemon can actually register? The keybind recorder asks
        before committing, so the window never stores something hotkeys.py will reject at
        startup. Reuses the real parser (hard rule 3) rather than re-listing the key vocabulary
        — a bare key, an unknown key, or two non-modifier keys all fail here exactly as they
        would there."""
        try:
            from backend.hotkeys import parse_binding
            parse_binding(combo)
            return True
        except (ValueError, ImportError):
            return False

    @Slot()
    def pauseHotkeys(self) -> None:
        """Hand the daemon's doors back while the key recorder is listening.

        Without this, `RegisterHotKey` has already consumed the combo machine-wide, so pressing
        the shortcut you are trying to rebind opens a turn and never reaches the field. Held as a
        lease (backend/hotkeys), so a window that dies mid-recording cannot leave the machine
        with no doors."""
        from backend.hotkeys import pause
        pause()

    @Slot()
    def resumeHotkeys(self) -> None:
        """Give the doors back the moment recording ends, rather than waiting out the lease."""
        from backend.hotkeys import resume
        resume()

    @Slot(str, result="QVariant")
    def rowsFor(self, pane: str) -> list:
        """The setting ids that render as an ordinary row on `pane`, in schema order.
        `models` is excluded — Model selection draws it as provider cards, not a row."""
        return [k for k, s in self.meta.items()
                if s.get("pane") == pane and s["type"] not in ("object",) and k != "primary"]

    @Slot(str, str, result="QVariant")
    def rowsInGroup(self, pane: str, group: str) -> list:
        """The rows of one titled group within a pane (General's Profile / Preferences)."""
        return [k for k in self.rowsFor(pane) if self.meta[k].get("group") == group]

    @Slot(str, result="QVariant")
    def groupsFor(self, pane: str) -> list:
        """A pane's titled groups, or [] when its rows are drawn flat."""
        for p in self.panes:
            if p["id"] == pane:
                return p.get("groups", [])
        return []

    @Slot(str, result="QVariant")
    def toolsFor(self, connector: str) -> list:
        """The tools a connector enables, as `[{label, ready}]` in registry order (D38).

        Read from `shared/schemas/tools.json`, never restated here (hard rule 3), so a tool joins a
        card by declaring the connector and nothing in this window changes. `ready` is false for a
        tool that could not run even with the connector on — no backend on this platform, or above
        `MAX_TIER` — because a card that promises what the tier still forbids is the same lie the
        gate exists to prevent. Consent has to be to something specific, which is why the card
        lists the tools rather than the category."""
        from shared.config import load_schemas
        from backend.tools import implemented

        return [{"label": t.get("label", t["name"]), "ready": implemented(t)}
                for t in load_schemas()["tools"]["tools"] if t.get("connector") == connector]

    # --- providers ----------------------------------------------------------------

    @Property("QVariant", notify=changed)
    def models(self) -> dict:
        """Providers the user has added: id -> {on, model, effort, thinking, ...}."""
        got = settings.get("models")
        return got if isinstance(got, dict) else {}

    @Property("QVariant", notify=changed)
    def addedProviders(self) -> list:
        """The provider ids in play — what a role (dictation cleanup, prompt cleanup) can be
        pointed at. Empty until a model is added."""
        return list(self.models.keys())

    @Property("QVariant", constant=True)
    def providerNames(self) -> dict:
        """Provider id -> the name to SHOW (`openai` -> `OpenAI`), from the catalogue's `name`.

        Settings store ids, which are lowercase wire names; a picker that prints them raw reads
        like a config file. Constant because the catalogue is (hard rule 3: it is the schema)."""
        return {pid: p.get("name", pid) for pid, p in self.catalog.items()}

    @Property("QVariant", constant=True)
    def effortLabels(self) -> dict:
        """Effort wire value -> the word to SHOW (`xhigh` -> `Extra`), from the schema.

        Same reasoning as `providerNames`: the values are wire words a provider chose and print
        like a config file. Constant because the schema is. A value with no entry falls through
        to the raw word in `Dropdown.shown()`, so a new level renders readably on day one."""
        return providers.schema().get("effort_labels", {})

    @Slot(str, result="QVariant")
    def providersFor(self, where: str) -> list:
        """Catalogue ids for one side of the Add flow: `cloud` or `local`."""
        return [pid for pid, p in self.catalog.items() if p.get("where") == where]

    @Slot(str, "QVariant")
    def addProvider(self, pid: str, config=None) -> None:
        """Add or update a provider. `config` carries whatever the sheet collected; anything it
        omits keeps its current value, so the one-argument call still works.

        An EDIT merges into the stored entry rather than rebuilding it: `on` and any field the form
        does not carry (a hand-added `keep_alive`) survive, and the default is NOT re-taken — a
        provider switched off must not come back on, and become primary, just because Done was
        pressed on its sheet. Only a genuinely NEW provider seeds the defaults and claims primary.
        """
        cat = self.catalog.get(pid)
        if cat is None:
            return
        # A QML object literal crosses as a QJSValue, NOT a dict — so an isinstance(dict) check
        # silently rejected it and every value the form collected was thrown away, leaving only
        # the schema fallbacks. Unwrap before anything reads it.
        if config is not None and not isinstance(config, dict):
            config = config.toVariant() if hasattr(config, "toVariant") else None
        added = dict(self.models)
        existing = added.get(pid)
        is_new = existing is None
        if is_new:
            caps = cat.get("capabilities", {})
            efforts = caps.get("effort") or []
            entry = {
                "on": True,
                # Fallback until the live fetch lands; may be empty (a local runner ships none).
                "model": (cat.get("models") or [""])[0],
                # `high` where offered — the provider default — else the top of a shorter scale.
                "effort": ("high" if "high" in efforts else efforts[-1]) if efforts else None,
                "thinking": False,
            }
            if cat.get("auth") == "endpoint":
                entry["endpoint"] = cat.get("endpoint", "")
        else:
            entry = dict(existing)          # keep `on`, and any field the form does not carry
        if isinstance(config, dict):
            # Allowlisted: a stray field (a key the form should never send to disk) is dropped, not
            # persisted. `v is not None` lets a capability the provider lacks (temperature on a
            # cloud card) be omitted rather than written.
            entry.update({k: v for k, v in config.items()
                          if v is not None and k in _PERSIST_FIELDS})
        added[pid] = entry
        settings.set("models", added)
        if is_new and not settings.get("primary"):
            settings.set("primary", pid)
        self.changed.emit()
        self.refreshModels(pid)     # so a freshly added card has a real picker, not an empty one

    @Slot(str)
    def removeProvider(self, pid: str) -> None:
        added = dict(self.models)
        added.pop(pid, None)
        settings.set("models", added)
        if settings.get("primary") == pid:
            live = [k for k, v in added.items() if v.get("on")]
            settings.set("primary", live[0] if live else "")
        # Clear any ROLE still naming the removed provider (cleanup_dictation / cleanup_prompts).
        # The router already falls back cleanly, but a stale pointer left in the file kept the
        # Dictate row displaying a provider that is no longer added — UI and daemon disagreeing
        # about a fact both can see. Derived from the schema, so a new provider-typed role is
        # covered without touching this (hard rule 3).
        for key, s in self.meta.items():
            if s.get("type") == "provider" and settings.get(key) == pid:
                settings.set(key, "")
        self.changed.emit()

    @Slot(str, str, "QVariant")
    def setModel(self, pid: str, field: str, value) -> None:
        """Change one field of one added provider (on / model / effort / thinking / …)."""
        if field not in _PERSIST_FIELDS:
            log.warning("setModel refused unknown field %r for %s", field, pid)
            return
        if value is not None and hasattr(value, "toVariant"):
            value = value.toVariant()      # QJSValue -> a real dict/list/scalar, as in set()
        added = dict(self.models)
        if pid not in added:
            return
        entry = dict(added[pid])
        entry[field] = value
        added[pid] = entry
        settings.set("models", added)
        # Turning off the primary hands the crown to another enabled provider, so the daemon
        # is never pointed at a provider the user just disabled.
        if field == "on" and not value and settings.get("primary") == pid:
            live = [k for k, v in added.items() if v.get("on")]
            settings.set("primary", live[0] if live else "")
        elif field == "on" and value and not settings.get("primary"):
            settings.set("primary", pid)
        self.changed.emit()

    @Slot(str, int)
    def moveProvider(self, pid: str, delta: int) -> None:
        """Move a model up or down the Ask list.

        The order IS the key order of the `models` object: JSON objects preserve insertion
        order in both the writer (Python 3.7+) and the reader (QML/JS, for string keys), so
        reordering means rewriting the dict rather than carrying a parallel index that could
        fall out of step with it.
        """
        ids = list(self.models.keys())
        if pid not in ids:
            return
        i = ids.index(pid)
        j = i + delta
        if not 0 <= j < len(ids):
            return                                # already at an end — nothing to do
        ids[i], ids[j] = ids[j], ids[i]
        added = self.models
        settings.set("models", {k: added[k] for k in ids})
        self.changed.emit()

    @Slot(str)
    def setPrimary(self, pid: str) -> None:
        settings.set("primary", pid)
        self.changed.emit()

    @Property("QVariant", notify=changed)
    def modelOptions(self) -> dict:
        """Every provider's pickable model ids, keyed by provider id: the live list once fetched,
        else the card's offline fallback.

        A PROPERTY, not just the slot below, for the same reason `keys` is one: QML re-evaluates a
        binding when a property it read changes, but a plain function call is not tracked. A
        dropdown bound to `modelsFor(id)` would therefore keep showing the empty offline list
        forever, even after a fetch landed and `changed` fired — which is exactly what it did.
        """
        return {pid: self.modelsFor(pid) for pid in self.catalog}

    @Slot(str, result="QVariant")
    def modelsFor(self, pid: str) -> list:
        """The model ids to offer for a provider: the live list once fetched, else the card's
        offline fallback. Never blocks — call `refreshModels` to go and look.

        Note the fallback is EMPTY for every provider but Anthropic (the cards ship no `models`),
        so without a fetch a picker has nothing to show.
        """
        with self._lock:
            live = self._live.get(pid)
        return live or self.catalog.get(pid, {}).get("models", [])

    @Slot(str, str, result=bool)
    def modelMissing(self, pid: str, model: str) -> bool:
        """Is `model` absent from a list this provider ACTUALLY gave us?

        Closes the loop a deleted model opened: the turn fails with `no_model` and tells the user to
        check settings, and settings then shows the stale name looking perfectly configured
        (commonest cause — `ollama rm` on a model a role was pointed at).

        The gate is `ok`, not "the list is empty". Before any fetch the cache is empty for every
        provider, so treating that as missing would flag the whole roster on every open — an alarm
        that fires when nobody has asked yet is worse than the silence it replaces. So: we asked, we
        got a list, this is not in it.
        """
        if not pid or not model:
            return False
        with self._lock:
            status, live = self._status.get(pid), self._live.get(pid)
        return status == "ok" and bool(live) and model not in live

    @Property(str, notify=changed)
    def localTwoModelNote(self) -> str:
        """The VRAM note, or "" when it does not apply (Thomas, 2026-08-02).

        Fires when two roles resolve to DIFFERENT models on the SAME local provider. That is the
        configuration where Ollama has to swap models in and out unless both fit in VRAM, and the
        failure is invisible: no error, just a full model reload on every switch between Ask and
        Dictate, which reads as "dictation is randomly slow".

        Deliberately NOT computed. Judging whether both actually fit would need total VRAM, current
        usage and each model's loaded footprint, and would be wrong often enough to be an
        annoyance — so this states the fact and leaves the judgement to someone who can see their
        own GPU. Same reason a cloud provider never triggers it: two remote models cost nothing to
        hold. The text comes from the schema, never restated here (hard rule 3).
        """
        by_provider: dict[str, set] = {}
        for role in ("primary", "cleanup_dictation", "cleanup_prompts"):
            pid = settings.get(role)
            card = self.catalog.get(pid, {}) if pid else {}
            if card.get("where") != "local":
                continue
            cfg = (settings.get("models") or {}).get(pid)
            if not isinstance(cfg, dict) or not cfg.get("on"):
                continue
            key = (settings.spec(role) or {}).get("modelKey")
            model = (str(settings.get(key) or "").strip() if key else "") or cfg.get("model")
            if model:
                by_provider.setdefault(pid, set()).add(model)
        if any(len(models) > 1 for models in by_provider.values()):
            return str((settings.spec("local_two_model_note") or {}).get("default") or "")
        return ""

    @Property("QVariant", notify=changed)
    def probeStates(self) -> dict:
        """Every provider's last probe outcome, keyed by id — a property so a binding that shows
        it re-evaluates when a fetch lands (same reason as `modelOptions`)."""
        return {pid: self.modelState(pid) for pid in self.catalog}

    @Slot(str, result=str)
    def modelState(self, pid: str) -> str:
        """What the window should say about this provider's models:

          `untested`     nobody has asked yet
          `fetching`     a probe is in flight
          `ok`           the provider answered with models
          `nokey`        no key stored
          `auth`         the provider REJECTED the key — the one a user must be told plainly
          `unreachable`  offline, or a local runner that isn't running
          `empty`        answered, but with nothing this account can use
          `error`        something else

        Anything but `ok` means the picker is showing the card's offline fallback, which is empty
        for every provider except Anthropic.
        """
        with self._lock:
            if pid in self._fetching:
                return "fetching"
            return self._status.get(pid, "untested")

    @Property("QVariant", notify=changed)
    def trial(self) -> dict:
        """The current trial: `{pid, status, models}`. Empty until a key is tested in the sheet."""
        return dict(self._trial)

    @Slot()
    def clearTrial(self) -> None:
        """Forget the last trial — called whenever the sheet opens or the typed key changes, so a
        verdict can never outlive the question that produced it."""
        with self._lock:
            self._trial_gen += 1          # orphan any answer still in flight
            self._trial = {"pid": "", "status": "", "models": []}
        self.changed.emit()

    @Slot(str, str)
    @Slot(str, str, str)
    def trialProvider(self, pid: str, key: str, endpoint: str = "") -> None:
        """Probe a TYPED credential and report into the trial slot alone, leaving the provider cache
        untouched. A failed trial clears the trial's model list: an empty picker is honest when the
        key just failed, and unlike the roster there is no list here worth protecting.

        `endpoint` is the address TYPED IN THE SHEET, for a LOCAL provider being added (its entry is
        not stored yet, so there is nothing to read it from). Cloud passes "" and the probe uses the
        catalogue's `api`."""
        cat = self.catalog.get(pid)
        if cat is None:
            return
        with self._lock:
            self._trial_gen += 1
            gen = self._trial_gen
            self._trial = {"pid": pid, "status": "fetching", "models": []}
        self.changed.emit()
        endpoint = (endpoint or "").strip() or (self.models.get(pid) or {}).get("endpoint")

        def work() -> None:
            try:
                found, status = providers.probe(pid, endpoint, key=key)
            except Exception as e:            # probe swallows its own, but never trust that
                log.warning("trial probe failed for %s: %s", pid, e)
                found, status = [], "error"
            with self._lock:
                if self._trial_gen != gen:    # a newer trial overtook this one — drop the answer
                    return
                self._trial = {"pid": pid, "status": status,
                               "models": found if status == "ok" else []}
            self.changed.emit()

        threading.Thread(target=work, daemon=True, name=f"trial-{pid}").start()

    @Slot(str)
    @Slot(str, str)
    def testProvider(self, pid: str, key: str = "") -> None:
        """Re-probe a provider even if we already hold its list — what the Test button calls.

        Deliberately forceful: the user pressing Test has usually just changed the key, and the
        point of the button is to find out whether the provider accepts it.

        `key` is the key TYPED IN THE FORM, which matters because the Add flow does not store a
        key until you commit — so testing the credential store would test the old key, or none.
        It is used for the probe and never written anywhere.
        """
        self._fetch(pid, force=True, key=key)

    @Slot(str)
    def refreshModels(self, pid: str) -> None:
        """Fetch a provider's model list if we have not already got one. Safe to call from a
        binding or `Component.onCompleted` — repeats are free."""
        self._fetch(pid, force=False)

    def _fetch(self, pid: str, force: bool, key: str = "") -> None:
        """Fetch a provider's real model list in the background.

        On a worker thread because the window must never freeze on someone else's network: the
        fetch is a plain blocking GET with a short timeout (providers.FETCH_TIMEOUT_S), and Qt
        signal emission is thread-safe, so the worker can announce itself directly.

        Two forms of idempotence, because QML rebuilds bindings freely: a fetch already in flight
        is never started twice, and a provider whose list we already hold is not re-fetched unless
        `force` (which is what saving a key does — the key is precisely what changes the answer).
        """
        cat = self.catalog.get(pid)
        if cat is None:
            return
        with self._lock:
            # `force` overtakes a probe already in flight rather than being swallowed by it: Test
            # is pressed precisely when the key or endpoint just changed, so the in-flight answer
            # is about to be wrong. Unforced fetches keep both cheap idempotences.
            if not force and (pid in self._fetching or pid in self._live):
                return
            gen = self._gen[pid] = self._gen.get(pid, 0) + 1
            self._fetching.add(pid)
        # A local runner's port is user-editable, so ask the entry before the catalogue default.
        endpoint = (self.models.get(pid) or {}).get("endpoint")

        def work() -> None:
            try:
                found, status = providers.probe(pid, endpoint, key=key)
            except Exception as e:                # probe swallows its own, but never trust that
                log.warning("model probe failed for %s: %s", pid, e)
                found, status = [], "error"
            with self._lock:
                if self._gen.get(pid) != gen:   # a newer probe overtook this one — drop the answer
                    log.info("model probe %s: superseded, discarding %s", pid, status)
                    return                      # and leave `_fetching` set: the newer one owns it
                self._fetching.discard(pid)
                self._status[pid] = status
                if found:
                    self._live[pid] = found
            log.info("model probe %s: %s (%d models)", pid, status, len(found))
            self.changed.emit()

        threading.Thread(target=work, name=f"nothal-models-{pid}", daemon=True).start()

    # --- credentials (never the settings file) ------------------------------------

    @Property("QVariant", notify=changed)
    def keys(self) -> dict:
        """Every provider's credential state, keyed by provider id. A PROPERTY rather than
        only the slot below, because QML re-evaluates a binding when a property it reads
        changes — a plain function call is not tracked, so a chip bound to `keyState(id)`
        would never refresh after a key was saved."""
        return {pid: self.keyState(pid) for pid in self.catalog}

    @Slot(str, result=str)
    def keyState(self, pid: str) -> str:
        """'stored', 'none', or 'unavailable' if the credential store cannot be read."""
        cat = self.catalog.get(pid, {})
        if cat.get("auth") != "key":
            return "none"
        try:
            import keyring
            return "stored" if keyring.get_password(KEY_SERVICE, cat["credential"]) else "none"
        except Exception as e:                    # a broken/locked backend must not crash us
            log.warning("credential store unreadable: %s", e)
            return "unavailable"

    @Slot(str, str, result=bool)
    def setKey(self, pid: str, value: str) -> bool:
        """Store or clear a provider's key. The value is never logged and never written
        anywhere but the OS credential store (spec/50 rule 10)."""
        cat = self.catalog.get(pid, {})
        if cat.get("auth") != "key":
            return False
        name = cat["credential"]
        try:
            import keyring
            import keyring.errors
            if value.strip():
                keyring.set_password(KEY_SERVICE, name, value.strip())
                log.info("key stored as (%s, %s)", KEY_SERVICE, name)
                # Saving a key is the moment the answer changes, so go and look straight away:
                # a picker that stays empty after a paste reads as "broken", not "unasked".
                # The Test button re-runs the same probe when the user wants to check by hand.
                self._fetch(pid, force=True)
            else:
                try:
                    keyring.delete_password(KEY_SERVICE, name)
                    log.info("key cleared for (%s, %s)", KEY_SERVICE, name)
                except keyring.errors.PasswordDeleteError:
                    pass          # THE no-op: PasswordDeleteError is keyring's "nothing stored".
                    # A LOCKED vault or an OS refusal raises a DIFFERENT type — those fall through
                    # to the outer except, so a delete the user asked for that did not happen is
                    # reported (False), not swallowed as success (the M6 lie).
                # Drop what the old key told us, or the picker would keep offering models this
                # profile can no longer reach.
                with self._lock:
                    self._live.pop(pid, None)
                    self._status.pop(pid, None)
            self.changed.emit()
            return True
        except Exception as e:
            log.error("could not write the credential store: %s", e)
            return False


if __name__ == "__main__":
    # ponytail: runnable check of the provider bookkeeping — the one place with real logic.
    # Points at a throwaway settings file; needs no Qt event loop and no credential store.
    import os
    import tempfile
    from pathlib import Path

    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication([])                    # QObject needs an application object

    # OFFLINE by construction (finding, 2026-08-02): addProvider auto-refreshes, so without this
    # every add here would read the REAL credential store and issue an authenticated GET on the
    # dev box — a check documented as needing "no credential store" quietly did neither. probe's
    # own network behaviour is providers' selfcheck; here it is stubbed to a deterministic offline
    # answer. Tests that need a specific probe result install their own stub over this one.
    _saved_probe = providers.probe
    providers.probe = lambda pid, endpoint=None, timeout=None, key=None: ([], "unreachable")

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["NOTHAL_SETTINGS"] = str(Path(tmp) / "s.json")
        m = SettingsModel()
        assert m.models == {}, "no providers before any are added"
        assert [g["id"] for g in m.groupsFor("general")] == ["profile", "preferences"]
        assert m.groupsFor("triggers") == [], "a flat pane declares no groups"
        assert m.rowsInGroup("general", "preferences") == \
            ["theme", "language", "pings", "listen_for_me", "tts", "dwell_quick", "dwell_slow",
             "al_in_island", "local_server_stop_on_quit"], \
            m.rowsInGroup("general", "preferences")
        # Every row of a grouped pane must land in a group, or it renders nowhere.
        grouped = sum(len(m.rowsInGroup("general", g["id"])) for g in m.groupsFor("general"))
        assert grouped == len(m.rowsFor("general")), "a General row is in no group"
        assert "models" not in m.rowsFor("models"), "cards are not rows"

        m.addProvider("anthropic")
        assert m.models["anthropic"]["on"] is True
        assert m.models["anthropic"]["effort"] == "high", "effort starts at the provider default"
        assert settings.get("primary") == "anthropic", "first added provider becomes primary"

        m.addProvider("groq")
        assert m.models["groq"]["effort"] is None, "no effort row for a provider without one"
        assert settings.get("primary") == "anthropic", "adding must not steal primary"

        m.setModel("anthropic", "on", False)
        assert settings.get("primary") == "groq", "disabling the primary must hand it on"
        m.setModel("groq", "on", False)
        assert settings.get("primary") == "", "nothing enabled -> no primary"

        m.setModel("groq", "on", True)
        assert settings.get("primary") == "groq", "re-enabling with no primary reclaims it"

        # Reorder: the key order of `models` is the display order.
        m.addProvider("openai")
        assert list(m.models) == ["anthropic", "groq", "openai"], list(m.models)
        m.moveProvider("openai", -1)
        assert list(m.models) == ["anthropic", "openai", "groq"], list(m.models)
        m.moveProvider("anthropic", -1)
        assert list(m.models) == ["anthropic", "openai", "groq"], "top cannot move up"
        m.moveProvider("groq", 1)
        assert list(m.models) == ["anthropic", "openai", "groq"], "bottom cannot move down"
        m.moveProvider("nosuch", 1)               # must not raise
        assert m.models["openai"]["on"] is True, "reordering must not disturb the entries"
        m.removeProvider("openai")

        m.removeProvider("groq")
        assert "groq" not in m.models and settings.get("primary") == ""

        # --- the live model fetch (no network) -------------------------------------------
        # Until a fetch lands, a picker shows the card's offline list and says so. That matters
        # because a fallback list goes stale silently: Anthropic's live list already carries
        # models this schema does not name.
        import time

        # `mistral` deliberately: the checks above added anthropic/groq/openai/ollama, and
        # addProvider now kicks its own probe, so those are no longer in a virgin state.
        assert m.modelsFor("mistral") == m.catalog["mistral"]["models"]
        assert m.modelState("mistral") == "untested", "nothing is claimed before anything asks"
        assert m.modelsFor("nosuch") == [], "an unknown provider offers nothing, never raises"
        m.refreshModels("nosuch")               # must not start a thread or raise

        # Every card must be answerable by the picker binding, or a dropdown renders undefined.
        assert set(m.modelOptions) == set(m.catalog)
        assert set(m.probeStates) == set(m.catalog)

        # A probe that finds nothing must LEAVE the fallback standing rather than blank the picker
        # the user is reading — and must say WHY, which is the whole point of the Test button.
        m.addProvider("ollama")                 # addProvider kicks its own (harmless) probe
        m.setModel("ollama", "endpoint", "127.0.0.1:1")
        m.testProvider("ollama")
        for _ in range(200):
            if m.modelState("ollama") != "fetching":
                break
            time.sleep(0.05)
        assert m.modelState("ollama") == "unreachable", \
            f"a dead runner must be nameable, got {m.modelState('ollama')!r}"
        assert m.modelsFor("ollama") == m.catalog["ollama"]["models"], "fallback must survive"

        # Test OVERTAKES a probe already in flight, and the overtaken answer is DISCARDED rather
        # than landing last. Both halves matter: without the first, pressing Test right after
        # adding a provider does nothing (the bug — `force` was swallowed by the in-flight guard);
        # without the second, the stale reply overwrites the fresh one and the user reads a status
        # for an endpoint they have already changed. Faked probes, so this needs no network and no
        # local runner: slow-and-wrong vs fast-and-right, deliberately returned out of order.
        real_probe = providers.probe
        try:
            first_started = threading.Event()

            def slow_then_fast(pid, endpoint=None, timeout=None, key=None):
                if endpoint == "127.0.0.1:2":
                    return [], "unreachable"          # the forced probe: immediate, correct
                first_started.set()
                time.sleep(0.5)                       # the overtaken probe: late, and wrong
                return [], "empty"

            providers.probe = slow_then_fast
            race = SettingsModel()
            race.addProvider("ollama")
            assert first_started.wait(3), "the first probe never started"
            race.setModel("ollama", "endpoint", "127.0.0.1:2")
            race.testProvider("ollama")               # must overtake, not be swallowed
            for _ in range(60):
                if race.modelState("ollama") == "unreachable":
                    break
                time.sleep(0.05)
            assert race.modelState("ollama") == "unreachable", \
                f"Test must overtake a probe in flight, got {race.modelState('ollama')!r}"
            time.sleep(0.8)                           # let the overtaken probe return late
            assert race.modelState("ollama") == "unreachable", \
                f"a superseded probe must not land last, got {race.modelState('ollama')!r}"
            race.removeProvider("ollama")
        finally:
            providers.probe = real_probe

        # ...and a probe that DID find something wins over the card, without another round trip.
        with m._lock:
            m._live["ollama"] = ["llama3.2:3b", "qwen3:8b"]
            m._status["ollama"] = "ok"
        assert m.modelState("ollama") == "ok"
        assert m.modelsFor("ollama") == ["llama3.2:3b", "qwen3:8b"], "the live list must win"
        assert m.modelOptions["ollama"] == ["llama3.2:3b", "qwen3:8b"], "the property must agree"
        # refreshModels must NOT re-fetch what we hold; testProvider must.
        m.refreshModels("ollama")
        assert m.modelState("ollama") == "ok", "a cached list is not re-fetched by refreshModels"
        m.removeProvider("ollama")

        # --- an EDIT merges, it does not rebuild (M2) ------------------------------------
        # commitAdd funnels an edit through addProvider(pid, config). A provider switched OFF must
        # not come back on — nor steal the default — just because Done was pressed on its sheet,
        # and a field the form does not carry must survive.
        settings.set("models", {}); settings.set("primary", "")
        em = SettingsModel()
        em.addProvider("openai")
        em.addProvider("groq")
        settings.set("primary", "groq")               # openai is the non-primary one we disable
        em.setModel("openai", "on", False)
        # a hand-added field the sheet has no control for
        _added = dict(em.models); _added["openai"] = {**_added["openai"], "keep_alive": "10m"}
        settings.set("models", _added)
        em.addProvider("openai", {"model": "gpt-x", "endpoint": None, "temperature": None})
        assert em.models["openai"]["on"] is False, "an edit must not re-enable a disabled provider"
        assert settings.get("primary") == "groq", "an edit must not steal the default"
        assert em.models["openai"]["model"] == "gpt-x", "the edited field lands"
        assert em.models["openai"].get("keep_alive") == "10m", "the merge keeps uncarried fields"

        # --- the persistable-field allowlist: no secret to the file (rule 10) ------------
        em.addProvider("groq", {"model": "g-1", "key": "sk-MUST-NOT-PERSIST",
                                "api_key": "sk-NOR-THIS"})
        _g = em.models["groq"]
        assert _g["model"] == "g-1"
        assert "key" not in _g and "api_key" not in _g, \
            f"a credential-shaped field must never reach settings.json: {_g}"
        em.setModel("groq", "key", "sk-STILL-NOT")
        assert "key" not in em.models["groq"], "setModel must refuse a non-persistable field"

        # --- removing a provider clears the roles that named it (M12) --------------------
        settings.set("cleanup_dictation", "groq")
        em.removeProvider("groq")
        assert settings.get("cleanup_dictation") == "", \
            "a role must not keep naming a removed provider"
        em.removeProvider("openai")

        # --- setKey never claims success on a real credential-store failure (M6) ---------
        # A fake keyring, injected in-process: a LOCKED vault refuses the delete, and setKey must
        # report that (False) rather than swallow it as success while the key stays in the store.
        import sys as _sys
        import types as _types

        class _FakeErrors:
            class PasswordDeleteError(Exception): pass
            class KeyringLocked(Exception): pass

        def _fake_keyring(delete_exc):
            k = _types.ModuleType("keyring")
            k.errors = _FakeErrors
            store = {(KEY_SERVICE, "anthropic"): "existing-key"}
            k.get_password = lambda s, n: store.get((s, n))
            k.set_password = lambda s, n, v: store.__setitem__((s, n), v)
            def _del(s, n):
                if delete_exc is not None:
                    raise delete_exc
                store.pop((s, n), None)
            k.delete_password = _del
            k._store = store
            return k

        _prev = _sys.modules.get("keyring"), _sys.modules.get("keyring.errors")
        try:
            settings.set("models", {}); settings.set("primary", "")
            km = SettingsModel()
            km.addProvider("anthropic")
            locked = _fake_keyring(_FakeErrors.KeyringLocked("vault locked"))
            _sys.modules["keyring"] = locked
            _sys.modules["keyring.errors"] = locked.errors
            assert km.setKey("anthropic", "") is False, \
                "a refused delete must report failure, not a swallowed success (M6)"
            assert locked._store.get((KEY_SERVICE, "anthropic")) == "existing-key", \
                "the key really is still stored — so setKey must not have said True"
            nothing = _fake_keyring(_FakeErrors.PasswordDeleteError("nothing stored"))
            _sys.modules["keyring"] = nothing
            _sys.modules["keyring.errors"] = nothing.errors
            assert km.setKey("anthropic", "") is True, \
                "PasswordDeleteError IS the legitimate nothing-stored no-op"
        finally:
            for _name, _mod in zip(("keyring", "keyring.errors"), _prev):
                if _mod is None:
                    _sys.modules.pop(_name, None)
                else:
                    _sys.modules[_name] = _mod

    # --- the two-model VRAM note (2026-08-02). Fires only for DIFFERENT models on the SAME local
    # provider: a cloud provider costs nothing to hold two of, and one model shared by both roles
    # never swaps. Nothing is computed about actual VRAM (see the property).
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["NOTHAL_SETTINGS"] = str(Path(tmp) / "n.json")
        n = SettingsModel()
        note = str((settings.spec("local_two_model_note") or {}).get("default"))

        settings.set("models", {"ollama": {"on": True, "model": "qwen3:8b"}})
        settings.set("primary", "ollama"); settings.set("cleanup_dictation", "ollama")
        settings.set("cleanup_dictation_model", "")
        assert n.localTwoModelNote == "", "one model serving both roles never swaps"

        settings.set("cleanup_dictation_model", "qwen3:14b")          # the role override differs
        assert n.localTwoModelNote == note, "two local models on one provider must warn"

        settings.set("models", {"groq": {"on": True, "model": "llama-3.3-70b-versatile"}})
        settings.set("primary", "groq"); settings.set("cleanup_dictation", "groq")
        settings.set("cleanup_dictation_model", "llama-3.1-8b-instant")
        assert n.localTwoModelNote == "", "a CLOUD provider holds no VRAM — never warn"

        settings.set("models", {"ollama": {"on": False, "model": "qwen3:8b"}})
        settings.set("primary", "ollama"); settings.set("cleanup_dictation", "ollama")
        settings.set("cleanup_dictation_model", "qwen3:14b")
        assert n.localTwoModelNote == "", "a switched-off card is not in play"

        # A stored model the provider no longer lists (`ollama rm`). The gate is a SUCCESSFUL
        # fetch: before one, every provider's list is empty and flagging them all would be noise.
        assert not n.modelMissing("ollama", "qwen3:8b"), "nobody has asked yet — must not flag"
        with n._lock:
            n._status["ollama"] = "ok"
            n._live["ollama"] = ["qwen3.5:9b"]
        assert n.modelMissing("ollama", "qwen3:8b"), "asked, answered, and it is not in the list"
        assert not n.modelMissing("ollama", "qwen3.5:9b"), "a model that IS listed is fine"
        assert not n.modelMissing("ollama", ""), "no model chosen is a different fault (no_model)"
        with n._lock:
            n._status["ollama"] = "unreachable"
        assert not n.modelMissing("ollama", "qwen3:8b"), \
            "a dead server tells us nothing about which models exist"

    os.environ.pop("NOTHAL_SETTINGS", None)
    providers.probe = _saved_probe
    print("settings_model selfcheck OK: add/remove/enable/primary bookkeeping, live model fetch "
          "falls back without blanking the picker, edits merge, the field allowlist keeps secrets "
          "out of the file, removed providers leave no stale role, and setKey reports a real "
          "credential-store failure")
