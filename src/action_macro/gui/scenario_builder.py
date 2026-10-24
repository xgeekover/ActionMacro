"""
action_macro.gui.scenario_builder
====================================
Visual card-based scenario editor list widget for ActionMacro automation.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from action_macro.core.key_mapping import build_key_chord
from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    ImageWaitParams,
    KeyInputParams,
    MouseClickParams,
    ScenarioModel,
)


class ActionCardWidget(QFrame):
    """Visual card widget representing a single ActionBlock."""

    clicked = Signal(str)             # action_id
    move_up_requested = Signal(str)   # action_id
    move_down_requested = Signal(str) # action_id
    delete_requested = Signal(str)    # action_id
    enabled_toggled = Signal(str, bool)  # action_id, new_state

    # Visual style configurations per action type: (accent_color, bg_tint, badge_text)
    TYPE_COLORS = {
        ActionType.MOUSE_CLICK: ("#1976D2", "#E3F2FD", "🖱 Click"),
        ActionType.KEY_INPUT:   ("#7B1FA2", "#F3E5F5", "⌨ Key"),
        ActionType.FIXED_WAIT:  ("#E65100", "#FFF3E0", "⏱ Wait"),
        ActionType.IMAGE_WAIT:  ("#00796B", "#E0F2F1", "🖼 Image"),
    }

    def __init__(self, action: ActionBlock, index: int, total_count: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.action_id = action.id
        self.action = action
        self.index = index
        self.total_count = total_count
        self.is_executing = False
        self.is_selected = False

        self._setup_ui()
        self.update_content(action, index, total_count)

    def _setup_ui(self) -> None:
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        # Drag handle / index badge
        self.lbl_index = QLabel(f"#{self.index + 1}")
        self.lbl_index.setFont(QFont("Monospace", 9, QFont.Weight.Bold))
        self.lbl_index.setStyleSheet("color: #555555;")
        layout.addWidget(self.lbl_index)

        # Enable/Disable Checkbox
        self.chk_enabled = QCheckBox()
        self.chk_enabled.setChecked(self.action.enabled)
        self.chk_enabled.toggled.connect(self._on_toggled)
        layout.addWidget(self.chk_enabled)

        # Type badge
        self.lbl_badge = QLabel()
        self.lbl_badge.setFont(QFont("Arial", 8, QFont.Weight.Bold))
        self.lbl_badge.setStyleSheet("padding: 2px 6px; border-radius: 4px;")
        layout.addWidget(self.lbl_badge)

        # Content Summary (Title + Description + Param Summary)
        content_box = QVBoxLayout()
        content_box.setSpacing(2)

        self.lbl_title = QLabel()
        self.lbl_title.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        content_box.addWidget(self.lbl_title)

        self.lbl_summary = QLabel()
        self.lbl_summary.setFont(QFont("Arial", 8))
        self.lbl_summary.setStyleSheet("color: #666666;")
        content_box.addWidget(self.lbl_summary)

        layout.addLayout(content_box, stretch=1)

        # Reorder buttons
        self.btn_up = QToolButton()
        self.btn_up.setText("▲")
        self.btn_up.setToolTip("Move Action Up")
        self.btn_up.clicked.connect(lambda: self.move_up_requested.emit(self.action_id))
        layout.addWidget(self.btn_up)

        self.btn_down = QToolButton()
        self.btn_down.setText("▼")
        self.btn_down.setToolTip("Move Action Down")
        self.btn_down.clicked.connect(lambda: self.move_down_requested.emit(self.action_id))
        layout.addWidget(self.btn_down)

        # Delete button
        self.btn_delete = QToolButton()
        self.btn_delete.setText("✕")
        self.btn_delete.setToolTip("Delete Action")
        self.btn_delete.setStyleSheet("color: #D32F2F; font-weight: bold;")
        self.btn_delete.clicked.connect(lambda: self.delete_requested.emit(self.action_id))
        layout.addWidget(self.btn_delete)

    def update_content(self, action: ActionBlock, index: int, total_count: int) -> None:
        """Refreshes card labels, badges, and button enablement."""
        self.action = action
        self.index = index
        self.total_count = total_count

        self.lbl_index.setText(f"#{index + 1}")
        self.btn_up.setEnabled(index > 0)
        self.btn_down.setEnabled(index < total_count - 1)
        self.chk_enabled.blockSignals(True)
        self.chk_enabled.setChecked(action.enabled)
        self.chk_enabled.blockSignals(False)

        # Style badge
        act_type = action.type if isinstance(action.type, ActionType) else ActionType.from_str(str(action.type))
        color, bg, badge_text = self.TYPE_COLORS.get(
            act_type, ("#555555", "#EEEEEE", str(action.type))
        )
        self.lbl_badge.setText(badge_text)
        self.lbl_badge.setStyleSheet(f"color: {color}; background-color: {bg}; border: 1px solid {color}; border-radius: 4px; padding: 2px 6px;")

        # Title
        title_text = action.description.strip() or f"{act_type.value.replace('_', ' ').title()}"
        self.lbl_title.setText(title_text)

        # Parameter summary text
        p = action.params
        if act_type == ActionType.MOUSE_CLICK:
            self.lbl_summary.setText(f"Coords: ({p['x']}, {p['y']}) | Button: {p['button']} | Clicks: {p['clicks']}")
        elif act_type == ActionType.KEY_INPUT:
            keys_str = "+".join(str(k) for k in build_key_chord(p.get('modifiers'), p['keys']))
            self.lbl_summary.setText(f"Keys: [{keys_str}] | Mode: {p['action']}")
        elif act_type == ActionType.FIXED_WAIT:
            sec = p['duration_ms'] / 1000.0
            self.lbl_summary.setText(f"Duration: {sec:.2f} s ({p['duration_ms']} ms)")
        elif act_type == ActionType.IMAGE_WAIT:
            tmpl_name = Path(p['template_path']).name if p.get('template_path') else "N/A"
            self.lbl_summary.setText(f"Template: {tmpl_name} | Mode: {p['mode']} | Timeout: {p['timeout_seconds']}s")

        self._refresh_style()

    def set_executing(self, executing: bool) -> None:
        """Sets active execution highlight."""
        self.is_executing = executing
        self._refresh_style()

    def set_selected(self, selected: bool) -> None:
        """Sets visual selection highlight."""
        self.is_selected = selected
        self._refresh_style()

    def _refresh_style(self) -> None:
        """Applies stylesheet according to selection, execution, and enabled states."""
        opacity = "1.0" if self.action.enabled else "0.55"

        if self.is_executing:
            self.setStyleSheet(f"""
                ActionCardWidget {{
                    background-color: #E8F8F0;
                    border: 2px solid #00E676;
                    border-radius: 6px;
                }}
            """)
        elif self.is_selected:
            self.setStyleSheet(f"""
                ActionCardWidget {{
                    background-color: #F0F7FF;
                    border: 2px solid #2196F3;
                    border-radius: 6px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                ActionCardWidget {{
                    background-color: #FFFFFF;
                    border: 1px solid #D0D7DE;
                    border-radius: 6px;
                }}
                ActionCardWidget:hover {{
                    border: 1px solid #90CAF9;
                }}
            """)

    def _on_toggled(self, checked: bool) -> None:
        self.action.enabled = checked
        self._refresh_style()
        self.enabled_toggled.emit(self.action_id, checked)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.action_id)
        super().mousePressEvent(event)


class ActionListWidget(QListWidget):
    """Specialized list widget supporting internal drag & drop and keyboard navigation."""

    order_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSpacing(4)
        self.setStyleSheet("""
            QListWidget {
                background-color: #F6F8FA;
                border: 1px solid #D0D7DE;
                border-radius: 6px;
                padding: 4px;
            }
            QListWidget::item {
                border: none;
                margin: 0px;
                padding: 0px;
            }
        """)

    def dropEvent(self, event) -> None:
        super().dropEvent(event)
        self.order_changed.emit()


class ScenarioBuilder(QWidget):
    """Main Scenario Builder widget managing the list of ActionBlocks."""

    block_selected = Signal(object)  # Optional[ActionBlock]
    scenario_changed = Signal()
    model_changed = Signal()         # Alias for MainWindow integration
    action_added = Signal(object)    # ActionBlock
    action_deleted = Signal(str)     # action_id

    def __init__(self, scenario: Optional[ScenarioModel] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.scenario = scenario or ScenarioModel(name="New Scenario")
        self._card_map: Dict[str, ActionCardWidget] = {}
        self._item_map: Dict[str, QListWidgetItem] = {}
        self._selected_id: Optional[str] = None
        self._active_index: Optional[int] = None

        self._setup_ui()
        self.populate_from_scenario(self.scenario)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Toolbar for adding actions
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        btn_mouse = QPushButton("+ Mouse Click")
        btn_mouse.setStyleSheet("background-color: #E3F2FD; color: #1565C0; font-weight: bold;")
        btn_mouse.clicked.connect(lambda: self.add_action_block(ActionType.MOUSE_CLICK))
        toolbar.addWidget(btn_mouse)

        btn_key = QPushButton("+ Key Input")
        btn_key.setStyleSheet("background-color: #F3E5F5; color: #6A1B9A; font-weight: bold;")
        btn_key.clicked.connect(lambda: self.add_action_block(ActionType.KEY_INPUT))
        toolbar.addWidget(btn_key)

        btn_wait = QPushButton("+ Fixed Wait")
        btn_wait.setStyleSheet("background-color: #FFF3E0; color: #E65100; font-weight: bold;")
        btn_wait.clicked.connect(lambda: self.add_action_block(ActionType.FIXED_WAIT))
        toolbar.addWidget(btn_wait)

        btn_image = QPushButton("+ Image Wait")
        btn_image.setStyleSheet("background-color: #E0F2F1; color: #00695C; font-weight: bold;")
        btn_image.clicked.connect(lambda: self.add_action_block(ActionType.IMAGE_WAIT))
        toolbar.addWidget(btn_image)

        layout.addLayout(toolbar)

        # Action cards list widget
        self.list_widget = ActionListWidget()
        self.list_widget.order_changed.connect(self._sync_model_from_list_order)
        self.list_widget.currentItemChanged.connect(self._on_list_item_changed)
        layout.addWidget(self.list_widget, stretch=1)

        # Empty state prompt
        self.lbl_empty = QLabel("No action blocks in scenario.\nClick an action button above to add steps.")
        self.lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_empty.setStyleSheet("color: #888888; font-size: 13px; padding: 40px;")
        layout.addWidget(self.lbl_empty)
        self.lbl_empty.hide()

    def set_scenario(self, scenario: ScenarioModel) -> None:
        """Sets active scenario and refreshes entire list."""
        self.scenario = scenario
        self.populate_from_scenario(scenario)

    def get_scenario(self) -> ScenarioModel:
        """Returns current scenario model."""
        return self.scenario

    def populate_from_scenario(self, scenario: ScenarioModel) -> None:
        """Reconstructs all card items from scenario actions."""
        self.clear_active_action()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self._card_map.clear()
        self._item_map.clear()
        self.list_widget.blockSignals(False)

        if not scenario.actions:
            self.lbl_empty.show()
            self.list_widget.hide()
            self._selected_id = None
            self.block_selected.emit(None)
            return

        self.lbl_empty.hide()
        self.list_widget.show()

        tot = len(scenario.actions)
        for idx, act in enumerate(scenario.actions):
            self._create_card_item(act, idx, tot)

        # Select first card if none selected
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _create_card_item(self, action: ActionBlock, index: int, total_count: int) -> None:
        card = ActionCardWidget(action, index, total_count)
        card.clicked.connect(self.select_action)
        card.move_up_requested.connect(self.move_action_up)
        card.move_down_requested.connect(self.move_action_down)
        card.delete_requested.connect(self.delete_action)
        card.enabled_toggled.connect(self._on_card_enabled_toggled)

        item = QListWidgetItem(self.list_widget)
        item.setSizeHint(card.sizeHint())
        item.setData(Qt.ItemDataRole.UserRole, action.id)

        self.list_widget.setItemWidget(item, card)
        self._card_map[action.id] = card
        self._item_map[action.id] = item

    def add_action_block(self, action_type: ActionType) -> ActionBlock:
        """Creates and appends a new action block with valid factory defaults."""
        defaults = {
            ActionType.MOUSE_CLICK: MouseClickParams(x=500, y=500, button="left", clicks=1, interval_ms=0, press_duration_ms=50),
            ActionType.KEY_INPUT: KeyInputParams(keys=["enter"], action="press", modifiers=None, press_duration_ms=50),
            ActionType.FIXED_WAIT: FixedWaitParams(duration_ms=1000),
            ActionType.IMAGE_WAIT: ImageWaitParams(template_path="templates/target.png", mode="appear", confidence=0.8, timeout_seconds=10.0, roi=None, timeout_action="raise"),
        }
        new_act = ActionBlock(
            id=str(uuid.uuid4()),
            type=action_type,
            params=defaults[action_type],
            enabled=True,
            description="",
        )

        curr_row = self.list_widget.currentRow()
        insert_idx = (curr_row + 1) if curr_row >= 0 else len(self.scenario.actions)
        self.scenario.add_action(new_act, index=insert_idx)

        self.populate_from_scenario(self.scenario)
        self.select_action(new_act.id)

        self.action_added.emit(new_act)
        self.scenario_changed.emit()
        self.model_changed.emit()
        return new_act

    def delete_action(self, action_id: str) -> None:
        """Deletes action block by ID and updates selection."""
        if not self.scenario.remove_action(action_id):
            return

        self.populate_from_scenario(self.scenario)
        self.action_deleted.emit(action_id)
        self.scenario_changed.emit()
        self.model_changed.emit()

    def move_action_up(self, action_id: str) -> None:
        """Moves action block up by 1 position."""
        act = self.scenario.get_action(action_id)
        if not act:
            return
        idx = self.scenario.actions.index(act)
        if idx > 0:
            self.scenario.move_action(idx, idx - 1)
            self.populate_from_scenario(self.scenario)
            self.select_action(action_id)
            self.scenario_changed.emit()
            self.model_changed.emit()

    def move_action_down(self, action_id: str) -> None:
        """Moves action block down by 1 position."""
        act = self.scenario.get_action(action_id)
        if not act:
            return
        idx = self.scenario.actions.index(act)
        if idx < len(self.scenario.actions) - 1:
            self.scenario.move_action(idx, idx + 1)
            self.populate_from_scenario(self.scenario)
            self.select_action(action_id)
            self.scenario_changed.emit()
            self.model_changed.emit()

    def select_action(self, action_id: str) -> None:
        """Selects a card and emits block_selected."""
        if action_id not in self._item_map:
            return
        item = self._item_map[action_id]
        if self.list_widget.currentItem() == item:
            act = self.scenario.get_action(action_id)
            self.block_selected.emit(act)
        else:
            self.list_widget.setCurrentItem(item)

    def _on_list_item_changed(self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]) -> None:
        if previous:
            prev_id = previous.data(Qt.ItemDataRole.UserRole)
            if prev_id in self._card_map:
                self._card_map[prev_id].set_selected(False)

        if current:
            curr_id = current.data(Qt.ItemDataRole.UserRole)
            self._selected_id = curr_id
            if curr_id in self._card_map:
                self._card_map[curr_id].set_selected(True)
            act = self.scenario.get_action(curr_id)
            self.block_selected.emit(act)
        else:
            self._selected_id = None
            self.block_selected.emit(None)

    def update_action_block(self, action: ActionBlock) -> None:
        """Synchronizes edits made from PropertyInspector back to the card."""
        if action.id in self._card_map:
            card = self._card_map[action.id]
            card.update_content(action, card.index, card.total_count)
            self.scenario_changed.emit()
            self.model_changed.emit()

    def update_block_display(self, block: ActionBlock) -> None:
        """Alias for update_action_block for MainWindow integration."""
        self.update_action_block(block)

    def set_active_action(self, index: Optional[int]) -> None:
        """Highlights card currently executing."""
        self.clear_active_action()
        if index is None or index < 0 or index >= self.list_widget.count():
            return
        self._active_index = index
        item = self.list_widget.item(index)
        if item:
            act_id = item.data(Qt.ItemDataRole.UserRole)
            if act_id in self._card_map:
                self._card_map[act_id].set_executing(True)
            self.list_widget.scrollToItem(item)

    def clear_active_action(self) -> None:
        """Clears active execution highlight from all cards."""
        if self._active_index is not None and 0 <= self._active_index < self.list_widget.count():
            item = self.list_widget.item(self._active_index)
            if item:
                act_id = item.data(Qt.ItemDataRole.UserRole)
                if act_id in self._card_map:
                    self._card_map[act_id].set_executing(False)
        for card in self._card_map.values():
            if card.is_executing:
                card.set_executing(False)
        self._active_index = None

    def highlight_active_action(self, index: Optional[int]) -> None:
        """Sets active executing action or clears if index is None or < 0."""
        if index is None or index < 0:
            self.clear_active_action()
        else:
            self.set_active_action(index)

    def _sync_model_from_list_order(self) -> None:
        """Synchronizes scenario.actions order after Drag & Drop move and re-attaches card widgets."""
        curr_item = self.list_widget.currentItem()
        curr_id = curr_item.data(Qt.ItemDataRole.UserRole) if curr_item else self._selected_id
        new_order: List[ActionBlock] = []
        tot = self.list_widget.count()
        self._item_map.clear()

        for i in range(tot):
            item = self.list_widget.item(i)
            act_id = item.data(Qt.ItemDataRole.UserRole)
            act = self.scenario.get_action(act_id)
            if act:
                new_order.append(act)
                self._item_map[act.id] = item
                if act.id in self._card_map:
                    card = self._card_map[act.id]
                    card.update_content(act, i, tot)
                else:
                    card = ActionCardWidget(act, i, tot)
                    card.clicked.connect(self.select_action)
                    card.move_up_requested.connect(self.move_action_up)
                    card.move_down_requested.connect(self.move_action_down)
                    card.delete_requested.connect(self.delete_action)
                    card.enabled_toggled.connect(self._on_card_enabled_toggled)
                    self._card_map[act.id] = card
                self.list_widget.setItemWidget(item, card)
                item.setSizeHint(card.sizeHint())

        self.scenario.actions = new_order
        if curr_id and curr_id in self._item_map:
            self.select_action(curr_id)
            if curr_id in self._card_map:
                self._card_map[curr_id].set_selected(True)
        elif self.list_widget.count() > 0:
            first_item = self.list_widget.item(0)
            if first_item:
                first_id = first_item.data(Qt.ItemDataRole.UserRole)
                if first_id:
                    self.select_action(first_id)

        self.scenario_changed.emit()
        self.model_changed.emit()

    def _on_card_enabled_toggled(self, action_id: str, enabled: bool) -> None:
        self.scenario_changed.emit()
        self.model_changed.emit()
