from __future__ import annotations
import hashlib
import json
import shutil
from os.path import basename, dirname, exists, join
from homeassistant.util.yaml import load_yaml
from .logger import _LOGGER
from . import devices as config_dir

# sha256 of every version of each packaged device YAML (devices/known_versions.json), to tell an
# untouched copy in <config> from one the user edited. Loaded once.
_KNOWN_VERSIONS: dict[str, set[str]] | None = None
# User copies already reported as edited (logged once per file, not on every refresh).
_REPORTED_COPIES: set[str] = set()


def _known_versions(name: str) -> set[str]:
    global _KNOWN_VERSIONS
    if _KNOWN_VERSIONS is None:
        try:
            with open(join(dirname(config_dir.__file__), "known_versions.json"), encoding="utf-8") as f:
                _KNOWN_VERSIONS = {k: set(v) for k, v in json.load(f).items()}
        except (OSError, ValueError) as e:
            _LOGGER.warning(f"Cannot read the known device description versions: {e}")
            _KNOWN_VERSIONS = {}
    return _KNOWN_VERSIONS.get(name, set())


def _digest(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _as_number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _yaml_text(value, true: str, false: str) -> str | None:
    # Unquoted on/off/yes/no are booleans in YAML 1.1: read them the way they were meant.
    if value is True:
        return true
    if value is False:
        return false
    return None if value in (None, "") else str(value)


class HaierDeviceConfig(object):

    def __init__(self, model: str, userpath: str = "") -> None:
        self._model = model
        self._userpath = userpath
        self._config = {}
        self._load_config_file()
        command_name = self._config.get('command_name')
        self._command_name = str(command_name) if command_name not in (None, "") else None
        self._attrs_cache = {}
        self.attrs = []
        self.constraint = Constraint([])
        self.modifiers: list[Modifier] = []

    def __getitem__(self, item) -> str | None:
        return self.get_code_by_name(str(item))

    @property
    def command_name(self) -> str:
        return self._command_name

    @command_name.setter
    def command_name(self, value: str) -> None:
        self._command_name = str(value)

    def _find_config_file(self) -> str:
        # The packaged description is used unless the user edited the copy kept in <config>
        # (made for exactly that). An untouched copy — of any earlier packaged version, or of the
        # default template made before the model had its own file — follows the package: before,
        # every copy won forever, so fixes in the package never reached existing installs.
        package_dir = dirname(config_dir.__file__)
        package = join(package_dir, self._model) + '.yaml'
        if not exists(package):
            package = join(package_dir, 'default') + '.yaml'
        user = f"{self._userpath}.{self._model}.yaml"
        try:
            if not exists(user):
                shutil.copy(package, user)
                return package
            digest = _digest(user)
            untouched = _known_versions(basename(package)) | _known_versions("default.yaml")
            if digest in untouched or digest == _digest(package):
                if digest != _digest(package):
                    shutil.copy(package, user)
                    _LOGGER.info(f"Updated {user}: it was an unmodified copy of an older version")
                return package
        except OSError as e:
            _LOGGER.warning(f"Device description copy {user}: {e}")
            return package
        if user not in _REPORTED_COPIES:
            _REPORTED_COPIES.add(user)
            _LOGGER.info(f"Using the edited device description {user} (delete it to use the packaged one)")
        return user

    def _load_config_file(self) -> None:
        fname = self._find_config_file()
        try:
            config = load_yaml(fname) or {}
            if not isinstance(config, dict):
                raise ValueError(f"expected a mapping at the top level, got {type(config).__name__}")
            self._config = config
        except Exception as e:
            _LOGGER.error(f"Failed to load config {fname}: {e}")
            self._config = {}
        else:
            _LOGGER.debug("Loaded device config %s", fname)

    def to_dict(self) -> dict:
        return {
            "command_name": self.command_name,
            "attributes": [a.to_dict() for a in self.attrs],
        }

    def get_attr_by_code(self, code: str | int) -> Attribute | None:
        return next(filter(
            lambda a: str(a.code) == str(code),
            self.attrs
        ), None)

    def get_attr_by_name(self, name: str) -> Attribute | None:
        if name in self._attrs_cache:
            return self._attrs_cache[name]
        attr = next(filter(lambda a: a.name == name, self.attrs), None)
        if attr is not None:
            self._attrs_cache[name] = attr
        return attr

    def get_value(self, name: str, code: str) -> str | None:
        attr = self.get_attr_by_name(name)
        return {str(i.value): i.name for i in attr.list}.get(str(code)) if attr else None

    def get_value_code(self, name: str, value: str) -> str | None:
        attr = self.get_attr_by_name(name)
        return {i.name: i.value for i in attr.list}.get(value) if attr else None

    def get_values(self, name: str) -> list[str]:
        attr = self.get_attr_by_name(name)
        return [i.name for i in attr.list if i.name != "unknown"] if attr else []

    def get_code_by_name(self, name: str) -> str | None:
        attr = self.get_attr_by_name(name)
        return str(attr.code) if attr else None

    def get_command_by_name(self, name: str) -> list[dict] | None:
        try:
            commands = self._config.get('commands') or {}
            return commands.get(name)
        except Exception:
            return None

    def get_config_attributes(self) -> list[Attribute]:
        # Tolerant of hand edits of the user copy: empty lists, unquoted on/off (YAML booleans),
        # numeric ids written without quotes (id: 0).
        attrs = []
        for attr in self._config.get('attributes') or []:
            if not isinstance(attr, dict) or attr.get("id") in (None, ""):
                continue
            mappings = []
            for m in attr.get("mappings") or []:
                if not isinstance(m, dict):
                    continue
                haier = _yaml_text(m.get('haier'), "true", "false")
                value = _yaml_text(m.get('value'), "on", "off")
                if haier is not None and value is not None:
                    mappings.append({"data": haier, "name": value, "attrname": value})
            attrs.append(Attribute({
                "attrname": attr.get("name"),
                "description": attr.get('description'),
                "name": str(attr.get('id')),
                "currentValue": attr.get('current'),
                "list": {"data": mappings},
            }))
        return attrs

    def merge_attributes(self) -> None:
        attributes = {a.name: a for a in self.get_config_attributes()}
        for i, attr in enumerate(self.attrs[:]):
            config_attr = attributes.pop(attr.name, None)
            if config_attr and attr.name == config_attr.name:
                config_attr.description = attr.description
                config_attr.current = attr.current
                config_attr.adopt(attr)
                config_attr.range = attr.range
                config_attr.list = config_attr.list or attr.list
                self.attrs[i] = config_attr
            if attr.command_name and not self.command_name:
                self.command_name = attr.command_name
        # An API attribute whose label has no canonical name, with a code the model YAML
        # describes: the YAML one replaces it. Otherwise get_attr_by_code() finds the unnamed
        # one first and the attribute's values are ignored.
        unnamed = {str(a.code): i for i, a in enumerate(self.attrs) if a.name == "unknown"}
        for name, config_attr in list(attributes.items()):
            i = unnamed.pop(str(config_attr.code), None)
            if i is None:
                continue
            attr = self.attrs[i]
            config_attr.description = attr.description
            config_attr.current = attr.current
            config_attr.adopt(attr)
            config_attr.range = attr.range
            config_attr.list = config_attr.list or attr.list
            self.attrs[i] = config_attr
            del attributes[name]
        self.attrs.extend(attributes.values())
        self.extend_attributes()
        self.attrs.sort(key=Attribute.sort_key)

    def extend_attributes(self) -> None:
        pass


class HaierACConfig(HaierDeviceConfig):

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"command_name={self.command_name!r},"
            f"current_temperature={self['current_temperature']!r},"
            f"target_temperature={self['target_temperature']!r},"
            f"status={self['status']!r},"
            f"mode={self['mode']!r},"
            f"fan_mode={self['fan_mode']!r},"
            f"swing_mode={self['swing_mode']!r},"
            f"swing_horizontal_mode={self['swing_horizontal_mode']!r}"
            f")"
        )

    @property
    def preset_mode(self) -> bool:
        return bool(self.get_preset_modes())

    def get_custom_preset_modes(self) -> list[str]:
        try:
            modes = self._config.get('preset_modes')
            assert isinstance(modes, list)
            assert len(modes) > 0
            assert all(((m and isinstance(m, str)) for m in modes))
            return modes
        except AssertionError:
            return []

    def get_preset_modes(self) -> list[str]:
        return self.get_custom_preset_modes() + [
            a.name.replace("preset_mode_", "")
            for a in filter(lambda a: a.name.startswith("preset_mode"), self.attrs)
        ]

    def extend_attributes(self) -> None:
        if (attr := self.get_attr_by_name("preset_mode_sleep")) and not self["quiet"]:
            attr_copy = attr.copy()
            attr_copy.update(attrname="quiet")
            self.attrs.append(Attribute(attr_copy))
        elif (attr := self.get_attr_by_name("quiet")) and not self["preset_mode_sleep"]:
            attr_copy = attr.copy()
            attr_copy.update(attrname="preset_mode_sleep")
            self.attrs.append(Attribute(attr_copy))
        if (attr := self.get_attr_by_name("preset_mode_boost")) and not self["turbo"]:
            attr_copy = attr.copy()
            attr_copy.update(attrname="turbo")
            self.attrs.append(Attribute(attr_copy))
        elif (attr := self.get_attr_by_name("turbo")) and not self["preset_mode_boost"]:
            attr_copy = attr.copy()
            attr_copy.update(attrname="preset_mode_boost")
            self.attrs.append(Attribute(attr_copy))


class HaierREFConfig(HaierDeviceConfig):

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"current_temperature={self['current_temperature']!r},"
            f"current_fridge_temperature={self['current_fridge_temperature']!r},"
            f"current_freezer_temperature={self['current_freezer_temperature']!r},"
            f"fridge_mode={self['fridge_mode']!r},"
            f"freezer_mode={self['freezer_mode']!r},"
            f"super_cooling={self['super_cooling']!r},"
            f"super_freeze={self['super_freeze']!r},"
            f"vacation_mode={self['vacation_mode']!r},"
            f"door_open={self['door_open']!r}"
            f")"
        )


class HaierWMConfig(HaierDeviceConfig):

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"status={self['status']!r},"
            f"program={self['program']!r},"
            f"temperature={self['temperature']!r},"
            f"spin_speed={self['spin_speed']!r},"
            f"remaining_hours={self['remaining_hours']!r},"
            f"remaining_minutes={self['remaining_minutes']!r}"
            f")"
        )

    def merge_attributes(self) -> None:
        # Washing machine attributes from the API have no names (description == "not found"),
        # so the YAML description is matched to API attributes by CODE, not by name.
        config_attrs = {str(a.code): a for a in self.get_config_attributes()}
        for i, attr in enumerate(self.attrs[:]):
            config_attr = config_attrs.pop(str(attr.code), None)
            if config_attr is not None:
                config_attr.current = attr.current
                config_attr.adopt(attr)
                config_attr.range = attr.range
                config_attr.list = config_attr.list or attr.list
                self.attrs[i] = config_attr
            if attr.command_name and not self.command_name:
                self.command_name = attr.command_name
        # YAML attributes with no matching API code are added as-is
        self.attrs.extend(config_attrs.values())
        self.attrs.sort(key=Attribute.sort_key)


class Attribute(dict):

    def __init__(self, data: dict) -> None:
        super().__init__(data)
        self.name = {
            # Air conditioners:
            "Режимы": "mode",
            "Целевая температура": "target_temperature",
            "Температура в комнате": "current_temperature",
            "Скорость вентилятора": "fan_mode",
            "Включение/выключение": "status",
            "Горизонтальные жалюзи": "swing_horizontal_mode",
            "Вертикальные жалюзи": "swing_mode",
            "Тихий": "preset_mode_sleep",
            "Турбо": "preset_mode_boost",
            "Комфортный сон": "comfort",
            "Здоровый режим": "health",
            "Звуковой сигнал": "sound",
            "Подсветка блока": "light",
            "10 градусов": "antifreeze",
            "Эко-датчик": "eco_sensor",
            "Стерильная очистка": "cleaning",
            "Авто влажность": "autohumidity",
            # Refrigerators:
            "Температура холодильника (℃)": "current_fridge_temperature",
            "Температура морозильной камеры (°C)": "current_freezer_temperature",
            "Температура в помещении": "current_temperature",
            "Холодильное отделение": "fridge_mode",
            "Морозильное отделение": "freezer_mode",
            "Супер-охлаждение": "super_cooling",
            "Супер-заморозка": "super_freeze",
            "Режим Отпуск": "vacation_mode",
            "Состояние дверцы холодильника": "door_open",
            "My Zone": "my_zone",
            # Washing machines:
            "Статус": "status",
            "Программа": "program",
            "Температура": "temperature",
            "Скорость отжима": "spin_speed",
            "Оставшееся время": "remaining_time",
        }.get(data.get("attrname", self.description), data.get("attrname") or "unknown")

    def __repr__(self) -> str:
        list_str = ",".join(repr(i) for i in self.list)
        return (
            f"{self.__class__.__name__}("
            f"{self.name}({self.code}),"
            f"desc={self.description!r},"
            f"current={self.current!r},"
            f"range={self.range!r},"
            f"list=[{list_str}]"
            f")"
        )

    def to_dict(self) -> dict:
        _range = self.range
        return {
            "name": self.name,
            "code": self.code,
            "description": self.description,
            "current": self.current,
            "range": _range.to_dict() if _range else None,
            "list": [l.to_dict() for l in self.list],
        }

    @property
    def description(self) -> str:
        return (self.get("description") or "").strip()

    @description.setter
    def description(self, value: str) -> None:
        self["description"] = value

    @property
    def code(self) -> int | str:
        # Numeric codes as int; a non-numeric one (e.g. "echoStatus") as is, so it neither
        # collides with others nor is sent as -1; -1 when there is none.
        name = self.get("name")
        try:
            return int(name)
        except (TypeError, ValueError):
            return str(name).strip() if name not in (None, "") else -1

    def adopt(self, api_attr: "Attribute") -> None:
        # What only the API knows about the attribute it describes (how writes are accepted).
        for key in ("operationType", "commandName", "writable"):
            if api_attr.get(key) is not None and self.get(key) is None:
                self[key] = api_attr[key]

    @staticmethod
    def sort_key(attr: "Attribute") -> tuple:
        code = attr.code
        return (0, code, "") if isinstance(code, int) else (1, 0, code)

    @property
    def current(self) -> int | float | str | None:
        return self.get("currentValue")

    @current.setter
    def current(self, value: int | float | str) -> None:
        self["currentValue"] = value

    @property
    def command_name(self) -> str | None:
        return self.get("commandName")

    @property
    def type(self) -> str:
        return self.get("type", "").lower()

    @property
    def list(self) -> list[Item]:
        data = (self.get("list") or {}).get("data")  # the API may send "list": null
        return [Item.create(self.name, v) for v in data if isinstance(v, dict)] if data else []

    @list.setter
    def list(self, value) -> None:
        self.setdefault("list", {})["data"] = value

    @property
    def range(self) -> Range | None:
        value = self.get("range")
        return Range(value) if value else None

    @range.setter
    def range(self, value: dict) -> None:
        self["range"] = value

    def get_item_code(self, name: str, default=None) -> str | None:
        # Return the raw default (not the string "None") when no item matches,
        # so callers can distinguish "no mapping" from a real value.
        item = next(filter(lambda i: str(i.name) == name, self.list), None)
        return str(item.value) if item is not None else default

    def get_item_name(self, code: str, default=None) -> str | None:
        # Normalize the incoming code the same way Item.value does (true/false -> 1/0),
        # otherwise an attribute whose raw values are "true"/"false" (e.g. WM status)
        # would never match the normalized item values and silently resolve to None.
        if code is None:
            return default
        code = {"true": "1", "false": "0"}.get(str(code), str(code))
        items = self.list
        item = next((i for i in items if str(i.value) == code), None)
        if item is None and (number := _as_number(code)) is not None:
            # "800.0" (WS) or a JSON number (REST) for the item "800"
            item = next((i for i in items if _as_number(i.value) == number), None)
        return str(item.name) if item is not None else default


class Range(dict):

    def __repr__(self) -> str:
        return (
            f"["
            f"min={self.min_value},"
            f"max={self.max_value},"
            f"step={self.step}"
            f"]"
        )

    def to_dict(self) -> dict:
        return {
            "min": self.min_value,
            "max": self.max_value,
            "step": self.step,
        }

    @property
    def type(self) -> str | None:
        return self.get("type")

    @property
    def data(self) -> dict:
        return self.get("data", {})

    @property
    def min_value(self) -> int | float | str | None:
        return self.data.get("minValue")

    @property
    def max_value(self) -> int | float | str | None:
        return self.data.get("maxValue")

    @property
    def step(self) -> int | float | str | None:
        return self.data.get("step")


class Item(dict):
    mappings = {
        "Выключен": "off",
        "Активен": "on",
    }

    def __init__(self, data: dict) -> None:
        super().__init__(data)
        self.name = self.mappings.get(
            self.description,
            data.get("attrname")
        ) or self.mappings.get("_") or "unknown"

    def __repr__(self) -> str:
        return (
            f"{self.name}("
            f"{self.value},"
            f"desc={self.description!r}"
            f")"
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "value": self.value,
        }

    @property
    def description(self) -> str:
        return (self.get("name") or "").strip()

    @property
    def value(self) -> str | None:
        value = self.get("data")
        return {
            "true": "1",
            "false": "0",
        }.get(value, value)

    @classmethod
    def create(cls, name: str, data: dict) -> Item:
        return {
            "mode": Mode,
            "fan_mode": FanMode,
            "swing_horizontal_mode": SwingHorizontalMode,
            "swing_mode": SwingMode,
            "eco_sensor": EcoSensor,
            "fridge_mode": Temperature,
            "freezer_mode": Temperature,
            "my_zone": Temperature,
        }.get(name, cls)(data)


class Mode(Item):
    mappings = {
        "Авто": "auto",
        "Охлаждение": "cool",
        "Нагрев": "heat",
        "Вентилятор": "fan_only",
        "Осушение": "dry",
    }


class FanMode(Item):
    mappings = {
        "Быстрый режим": "high",
        "Средний режим": "medium",
        "Медленный режим": "low",
        "Автоматический режим": "auto",
    }


class SwingHorizontalMode(Item):
    mappings = {
        "Исходная позиция": "position_1",
        "Второе положение поворота": "position_2",
        "Третье положение поворота": "position_3",
        "Четвертая позиция поворота": "position_4",
        "Пятая позиция поворота": "position_5",
        "Шестое положение поворота": "position_6",
        "Седьмая позиция поворота": "position_7",
        "Авто режим": "auto",
        "Выключен": "off",
    }


class SwingMode(Item):
    mappings = {
        "Фиксированное верхнее и нижнее положение": "off",
        "Верхнее положение": "upper",
        "Первое положение поворота": "position_1",
        "Нижнее положение": "bottom",
        "Второе положение поворота": "position_2",
        "Третье положение поворота": "position_3",
        "Вверх и вниз четвертая позиция": "position_4",
        "Пятая позиция поворота": "position_5",
        "Авто режим": "auto",
        "Автоматический подъем и опускание (только для специальной модели)": "special",
    }


class EcoSensor(Item):
    mappings = {
        "Выключен": "off",
        "Обводящий": "outlining",
        "Сопутствующий": "related",
        "Активен": "on",
    }


class Temperature(Item):
    def __init__(self, data: dict) -> None:
        description = (data.get("name") or "").strip()
        # Copy onto the instance first: Temperature has no own `mappings`, so
        # `self.mappings` is the shared Item.mappings class dict — mutating it would
        # leak every fridge/freezer temperature label into every other device's mappings.
        self.mappings = dict(self.mappings)
        self.mappings.setdefault(
            description,
            # the API writes both "-18℃" and "-18°C" (Latin or Cyrillic C)
            description.replace("℃", "").replace("°C", "").replace("°С", "").strip()
        )
        super().__init__(data)


class Modifier(dict):
    """An API "modifier": while its trigger holds (attribute codes having one of the listed
    values), its actions make attributes read-only ("W") or restrict their values ("V") — the Evo
    app greys these controls out. Triggers on device alarms only are not evaluated (the
    integration does not track alarms)."""

    @staticmethod
    def norm(value) -> str:
        # "true"/"false" -> "1"/"0", "24.00" -> "24": the forms the API and the device use.
        text = Constraint.value2code(str(value).strip()) if value is not None else ""
        try:
            number = float(text)
        except ValueError:
            return text
        return str(int(number)) if number.is_integer() else text

    def conditions(self) -> list[tuple[str, set[str]]]:
        trigger = self.get("trigger") or {}
        items = (trigger.get("conditions") or {}).get("condition") or []
        return [
            (str(c["mode"]), {self.norm(v) for v in (c.get("values") or [])})
            for c in items if isinstance(c, dict) and c.get("mode") is not None
        ]

    def active(self, state: dict) -> bool:
        conditions = self.conditions()
        if not conditions:
            return False
        hits = [state.get(code) in values for code, values in conditions]
        operator = str((self.get("trigger") or {}).get("operator") or "AND").upper()
        return any(hits) if operator == "OR" else all(hits)

    def _actions(self, kind: str) -> list[dict]:
        return [
            a for a in (self.get("actions") or [])
            if isinstance(a, dict) and a.get("name") is not None
            and str(a.get("rewriteFields", "")).upper() == kind
        ]

    def read_only(self) -> set[str]:
        return {str(a["name"]) for a in self._actions("W") if a.get("writable") is False}

    def ranges(self) -> dict[str, set[str]]:
        ranges = {}
        for a in self._actions("V"):
            data = (((a.get("valueRange") or {}).get("list") or {}).get("data")) or []
            values = {self.norm(i["data"]) for i in data if isinstance(i, dict) and i.get("data") is not None}
            if values:
                ranges[str(a["name"])] = values
        return ranges

    def describe(self) -> str:
        return " and ".join(f"{code}={'/'.join(sorted(values))}" for code, values in self.conditions())


class Constraint(list):

    def __init__(self, data: list) -> None:
        super().__init__(data)

    def apply(self, commands: list[dict]) -> list[dict]:
        if len(self) == 0 or not commands:
            return commands
        new_commands = []
        for command in commands:
            new_commands.extend(self.merge_commands(
                command,
                self.find_additionals(command)
            ))
        return new_commands

    def format_command(self, command: dict) -> dict:
        value = self.value2code(command.get("value"))
        return {
            "commandName": command.get("name"),
            "value": value,
        }

    def find_additionals(self, command: dict) -> list[dict]:
        command_code = command.get("commandName")
        command_value = command.get("value")
        additionals = []
        for item in self:
            cond = item.get("pendingCondition", {})
            for cond_command in cond.get("commands", []):
                if command_code != cond_command.get("commandName"):
                    continue
                values = [self.value2code(x) for x in (cond_command.get("values") or [])]
                if command_value not in values:
                    continue
                additionals.append(item.get("additionalCommands", {}))
        return additionals

    def merge_commands(self, command: dict, additionals: list[dict]) -> list[dict]:
        commands = {"PREPEND": [], "APPEND": []}
        for additional in additionals:
            merge_type = additional.get("mergeType")
            additional_commands = [self.format_command(x) for x in additional.get("commands", [])]
            target = commands.get(merge_type, [])
            target.extend(filter(
                lambda c: c not in target,
                additional_commands
            ))
        return [*commands["PREPEND"], command, *commands["APPEND"]]

    @staticmethod
    def value2code(value: str) -> str:
        return {"true": "1", "false": "0"}.get(value, value)
