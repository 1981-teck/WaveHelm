from __future__ import annotations

from types import SimpleNamespace


class FakeEvent:
    def __init__(self, value=None):
        self.value = value

    def GetColumn(self):
        return self.value


class FakeCloseEvent:
    def __init__(self, can_veto: bool = True):
        self._can_veto = can_veto
        self.vetoed = False
        self.skipped = False

    def CanVeto(self) -> bool:
        return self._can_veto

    def Veto(self) -> None:
        self.vetoed = True

    def Skip(self) -> None:
        self.skipped = True


class FakeSizeEvent:
    def __init__(self):
        self.skipped = False

    def Skip(self) -> None:
        self.skipped = True


class FakeMoveEvent:
    def __init__(self):
        self.skipped = False

    def Skip(self) -> None:
        self.skipped = True


class FakeMouseEvent:
    def __init__(self, x: int = 0, y: int = 0):
        self._x = int(x)
        self._y = int(y)
        self.skipped = False

    def GetX(self) -> int:
        return self._x

    def GetPosition(self):
        return (self._x, self._y)

    def Skip(self) -> None:
        self.skipped = True


class FakeWxBase:
    _next_handle = 1000

    def __init__(self) -> None:
        self._bindings = {}
        self._shown = True
        self.background = None
        self.foreground = None
        self.label = ''
        self.refreshed = 0
        self.show_calls = []
        self.focus_calls = 0
        self.enabled = True
        self.size = (1280, 720)
        self.position = (0, 0)
        self.screen_position = (0, 0)
        self.raise_calls = 0
        self._handle = FakeWxBase._next_handle
        FakeWxBase._next_handle += 1
        self.min_size = None
        self.initial_size = None

    def Bind(self, event_type, handler):
        self._bindings[event_type] = handler

    def trigger(self, event_type, event=None):
        handler = self._bindings[event_type]
        handler(event)

    def Show(self, value=True):
        self.show_calls.append(bool(value))
        self._shown = bool(value)

    def Hide(self):
        self._shown = False

    def IsShown(self):
        return self._shown

    def SetBackgroundColour(self, value):
        self.background = value

    def SetForegroundColour(self, value):
        self.foreground = value

    def Refresh(self):
        self.refreshed += 1

    def Raise(self):
        self.raise_calls += 1

    def SetFocus(self):
        self.focus_calls += 1

    def SetFocusIgnoringChildren(self):
        self.focus_calls += 1

    def Enable(self, value=True):
        self.enabled = bool(value)

    def IsEnabled(self):
        return self.enabled

    def SetLabel(self, label):
        self.label = label

    def SetMinSize(self, size):
        self.min_size = tuple(size)

    def SetInitialSize(self, size):
        self.initial_size = tuple(size)

    def InvalidateBestSize(self):
        return None

    def GetBestSize(self):
        text = getattr(self, 'label', '') or getattr(self, 'value', '') or ''
        width = max(48, 16 + len(str(text)) * 8)
        return (width, 28)

    def GetHandle(self):
        return self._handle

    def GetParent(self):
        return getattr(self, 'parent', None)

    def SetSize(self, size):
        self.size = tuple(size)
        if 'EVT_SIZE' in self._bindings:
            self.trigger('EVT_SIZE', FakeSizeEvent())

    def GetSize(self):
        return self.size

    def SetPosition(self, position):
        self.position = tuple(position)
        if 'EVT_MOVE' in self._bindings:
            self.trigger('EVT_MOVE', FakeMoveEvent())

    def GetPosition(self):
        return self.position

    def SetScreenPosition(self, position):
        self.screen_position = tuple(position)

    def GetScreenPosition(self):
        if self.screen_position != (0, 0):
            return self.screen_position
        parent = getattr(self, 'parent', None)
        parent_getter = getattr(parent, 'GetScreenPosition', None) if parent is not None else None
        if callable(parent_getter):
            parent_position = parent_getter()
            try:
                return (int(parent_position[0]) + int(self.position[0]), int(parent_position[1]) + int(self.position[1]))
            except (TypeError, ValueError, IndexError):
                return self.screen_position
        return self.screen_position

    def GetScreenRect(self):
        screen_position = self.GetScreenPosition()
        return SimpleNamespace(x=screen_position[0], y=screen_position[1], width=self.size[0], height=self.size[1])

    def Layout(self):
        return None

    def SetClientSize(self, size):
        self.SetSize(size)

    def GetClientSize(self):
        return self.size


class FakePanel(FakeWxBase):
    def __init__(self, parent=None):
        super().__init__()
        self.parent = parent
        self.sizer = None
        self.owner = None

    def SetSizer(self, sizer):
        self.sizer = sizer


class FakeScrolledWindow(FakePanel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scroll_rate = (0, 0)

    def SetScrollRate(self, x, y):
        self.scroll_rate = (x, y)


class FakeFrame(FakeWxBase):
    def __init__(self, parent=None, title="", style=0):
        super().__init__()
        self.parent = parent
        self.title = title
        self.style = style
        self.size = None
        self.min_size = None
        self.destroy_calls = 0
        self.fullscreen = False
        self.raise_calls = 0
        self.activate_calls = 0
        self.minimized = False

    def SetSize(self, size):
        super().SetSize(size)

    def SetPosition(self, position):
        super().SetPosition(position)
        self.screen_position = tuple(position)

    def SetMinSize(self, size):
        self.min_size = size

    def Centre(self):
        return None

    def Destroy(self):
        self.destroy_calls += 1

    def SetTitle(self, value):
        self.title = value

    def Raise(self):
        self.raise_calls += 1

    def Activate(self):
        self.activate_calls += 1

    def IsMinimized(self):
        return self.minimized

    def ShowNormal(self):
        self.minimized = False
        self._shown = True

    def ShowFullScreen(self, value):
        self.fullscreen = bool(value)

    def IsFullScreen(self):
        return self.fullscreen

    def Close(self):
        if 'EVT_CLOSE' in self._bindings:
            self.trigger('EVT_CLOSE', FakeCloseEvent(can_veto=False))
        else:
            self.Destroy()


class FakeSizer:
    def __init__(self, orientation):
        self.orientation = orientation
        self.items = []

    def Add(self, item, proportion=0, flags=0, border=0):
        if flags & FakeWxModule.EXPAND and flags & FakeWxModule.ALIGN_CENTER_VERTICAL:
            raise AssertionError('Invalid wx sizer flags: EXPAND cannot be combined with ALIGN_CENTER_VERTICAL in box sizers.')
        if self.orientation == FakeWxModule.VERTICAL and flags & FakeWxModule.ALIGN_CENTER_VERTICAL:
            raise AssertionError(
                'Invalid wx sizer flags: ALIGN_CENTER_VERTICAL cannot be used for children inside a vertical box sizer.'
            )
        self.items.append((item, proportion, flags, border))


class FakeStaticText(FakeWxBase):
    def __init__(self, parent=None, label=""):
        super().__init__()
        self.parent = parent
        self.label = label


class FakeButton(FakeWxBase):
    def __init__(self, parent=None, label=""):
        super().__init__()
        self.parent = parent
        self.label = label

    def click(self):
        self.trigger("EVT_BUTTON", None)




class FakeTextCtrl(FakeWxBase):
    def __init__(self, parent=None, value=''):
        super().__init__()
        self.parent = parent
        self.value = value
        self.hint = ''

    def GetValue(self):
        return self.value

    def SetValue(self, value):
        self.value = str(value)

    def SetHint(self, value):
        self.hint = str(value)


class FakeListCtrl(FakeWxBase):
    def __init__(self, parent=None, style=0):
        super().__init__()
        self.parent = parent
        self.style = style
        self.columns = []
        self.column_widths = []
        self.rows = []
        self.row_backgrounds = []
        self.selected_indices = set()

    def InsertColumn(self, index, heading, width=-1):
        while len(self.columns) <= index:
            self.columns.append('')
            self.column_widths.append(-1)
        self.columns[index] = heading
        self.column_widths[index] = width

    def SetColumn(self, index, heading):
        self.InsertColumn(index, heading, self.column_widths[index] if index < len(self.column_widths) else -1)

    def SetColumnWidth(self, index, width):
        while len(self.column_widths) <= index:
            self.column_widths.append(-1)
        self.column_widths[index] = width

    def GetColumnWidth(self, index):
        return self.column_widths[index]

    def DeleteAllItems(self):
        self.rows = []
        self.row_backgrounds = []
        self.selected_indices.clear()

    def InsertItem(self, index, text):
        row = ['' for _ in self.columns] or ['']
        row[0] = str(text)
        if index >= len(self.rows):
            self.rows.append(row)
            self.row_backgrounds.append(None)
            return len(self.rows) - 1
        self.rows.insert(index, row)
        self.row_backgrounds.insert(index, None)
        self.selected_indices = {i + 1 if i >= index else i for i in self.selected_indices}
        return index

    def SetItem(self, index, column, text):
        while len(self.rows[index]) <= column:
            self.rows[index].append('')
        self.rows[index][column] = str(text)

    def GetItemText(self, index, column=0):
        return self.rows[index][column]

    def GetItemCount(self):
        return len(self.rows)

    def SetItemBackgroundColour(self, index, value):
        while len(self.row_backgrounds) <= index:
            self.row_backgrounds.append(None)
        self.row_backgrounds[index] = value

    def GetItemBackgroundColour(self, index):
        if 0 <= index < len(self.row_backgrounds):
            return self.row_backgrounds[index]
        return None

    def Select(self, index, on=True):
        if on:
            self.selected_indices.add(index)
        else:
            self.selected_indices.discard(index)

    def GetFirstSelected(self):
        return min(self.selected_indices) if self.selected_indices else -1

    def GetNextSelected(self, current):
        for index in sorted(self.selected_indices):
            if index > current:
                return index
        return -1

    def activate(self, index):
        self.Select(index, True)
        self.trigger('EVT_LIST_ITEM_ACTIVATED', FakeEvent(index))

    def select_and_trigger(self, index):
        self.Select(index, True)
        if 'EVT_LIST_ITEM_SELECTED' in self._bindings:
            self.trigger('EVT_LIST_ITEM_SELECTED', FakeEvent(index))


class FakeFileDialog:
    next_paths = []
    next_result = 1

    def __init__(self, parent=None, message='', **kwargs):
        self.parent = parent
        self.message = message
        self.kwargs = dict(kwargs)
        self.destroy_calls = 0

    def ShowModal(self):
        return type(self).next_result

    def GetPaths(self):
        return list(type(self).next_paths)

    def GetPath(self):
        return str(type(self).next_paths[0]) if type(self).next_paths else ''

    def Destroy(self):
        self.destroy_calls += 1


class FakeDirDialog(FakeFileDialog):
    pass


class FakeChoice(FakeWxBase):
    def __init__(self, parent=None):
        super().__init__()
        self.parent = parent
        self.items = []
        self.selection = -1

    def SetItems(self, items):
        self.items = list(items)
        if not self.items:
            self.selection = -1

    def SetSelection(self, index):
        self.selection = index

    def GetSelection(self):
        return self.selection

    def GetCount(self):
        return len(self.items)

    def GetString(self, index):
        return self.items[index]

    def GetStringSelection(self):
        if 0 <= self.selection < len(self.items):
            return self.items[self.selection]
        return ''

    def SetStringSelection(self, value):
        if value in self.items:
            self.selection = self.items.index(value)
            return True
        return False

    def GetBestSize(self):
        probe = max((str(item) for item in self.items), key=len, default='')
        selection = self.GetStringSelection()
        if len(selection) > len(probe):
            probe = selection
        width = max(80, 24 + len(probe) * 8)
        return (width, 28)


class FakeSlider(FakeWxBase):
    def __init__(self, parent=None, value=0, minValue=0, maxValue=100):
        super().__init__()
        self.parent = parent
        self.value = value
        self.minValue = minValue
        self.maxValue = maxValue
        self.size = (200, 24)

    def GetValue(self):
        return self.value

    def GetMax(self):
        return self.maxValue

    def GetMin(self):
        return self.minValue

    def SetValue(self, value):
        self.value = value


class FakeCheckBox(FakeWxBase):
    def __init__(self, parent=None, label=''):
        super().__init__()
        self.parent = parent
        self.label = label
        self.value = False

    def GetValue(self):
        return self.value

    def SetValue(self, value):
        self.value = bool(value)


class FakeHtmlWindow(FakeWxBase):
    def __init__(self, parent=None):
        super().__init__()
        self.parent = parent
        self.html = ''

    def SetPage(self, html):
        self.html = html


class FakeSimplebook(FakePanel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pages = []
        self.selection = -1

    def AddPage(self, page, _title):
        self.pages.append(page)

    def SetSelection(self, index):
        self.selection = index




class FakeTextEntryDialog:
    next_value = ''
    next_result = 1

    def __init__(self, parent=None, message='', caption='', value=''):
        self.parent = parent
        self.message = message
        self.caption = caption
        self.value = value
        self.destroy_calls = 0

    def ShowModal(self):
        return type(self).next_result

    def GetValue(self):
        return str(type(self).next_value)

    def GetTextValue(self):
        return self.GetValue()

    def Destroy(self):
        self.destroy_calls += 1


class FakeCallLater:
    def __init__(self, delay, callback, *args):
        self.delay = delay
        self.callback = callback
        self.args = args
        self.stopped = False

    def Stop(self):
        self.stopped = True

    def run(self):
        if not self.stopped:
            self.callback(*self.args)


class FakeApp:
    def __init__(self, _redirect=False):
        self.top_window = None
        self.main_loop_calls = 0
        self.exit_main_loop_calls = 0
        self.app_name = None
        self.display_name = None
        self.vendor_name = None

    def SetAppName(self, value):
        self.app_name = value

    def SetAppDisplayName(self, value):
        self.display_name = value

    def SetVendorName(self, value):
        self.vendor_name = value

    def SetTopWindow(self, window):
        self.top_window = window

    def MainLoop(self):
        self.main_loop_calls += 1
        return 0

    def ExitMainLoop(self):
        self.exit_main_loop_calls += 1


class FakeWxModule:
    mouse_position = (0, 0)
    EVT_BUTTON = "EVT_BUTTON"
    EVT_CLOSE = "EVT_CLOSE"
    EVT_SIZE = "EVT_SIZE"
    EVT_MOVE = "EVT_MOVE"
    EVT_CHOICE = "EVT_CHOICE"
    EVT_TEXT = "EVT_TEXT"
    EVT_SLIDER = "EVT_SLIDER"
    EVT_LEFT_DOWN = "EVT_LEFT_DOWN"
    EVT_MOTION = "EVT_MOTION"
    EVT_ENTER_WINDOW = "EVT_ENTER_WINDOW"
    EVT_CHECKBOX = "EVT_CHECKBOX"
    EVT_LIST_ITEM_ACTIVATED = "EVT_LIST_ITEM_ACTIVATED"
    EVT_LIST_ITEM_SELECTED = "EVT_LIST_ITEM_SELECTED"
    EVT_LIST_COL_END_DRAG = "EVT_LIST_COL_END_DRAG"
    VERTICAL = 1
    HORIZONTAL = 2
    ALL = 4
    EXPAND = 8
    ALIGN_CENTER_VERTICAL = 16
    ALIGN_CENTER_HORIZONTAL = 32
    LC_REPORT = 24
    OK = 32
    ID_OK = 1
    ID_CANCEL = 0
    YES = 1
    NO = 0
    YES_NO = 64
    ICON_QUESTION = 128
    FD_SAVE = 256
    FD_OVERWRITE_PROMPT = 512
    App = FakeApp
    Frame = FakeFrame
    PopupWindow = FakeFrame
    Panel = FakePanel
    ScrolledWindow = FakeScrolledWindow
    BoxSizer = FakeSizer
    WrapSizer = FakeSizer
    StaticText = FakeStaticText
    Button = FakeButton
    TextCtrl = FakeTextCtrl
    Choice = FakeChoice
    Slider = FakeSlider
    CheckBox = FakeCheckBox
    ListCtrl = FakeListCtrl
    FileDialog = FakeFileDialog
    DirDialog = FakeDirDialog
    TextEntryDialog = FakeTextEntryDialog
    Simplebook = FakeSimplebook
    html = SimpleNamespace(HtmlWindow=FakeHtmlWindow)

    @staticmethod
    def CallAfter(callback, *args):
        callback(*args)

    @staticmethod
    def CallLater(delay, callback, *args):
        return FakeCallLater(delay, callback, *args)

    @staticmethod
    def GetMousePosition():
        return FakeWxModule.mouse_position

    next_message_box_result = YES

    @staticmethod
    def MessageBox(message, title, style=0):
        return FakeWxModule.next_message_box_result
