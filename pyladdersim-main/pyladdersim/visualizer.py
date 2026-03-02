import tkinter as tk
from collections.abc import Callable

from pyladdersim.visualize_shapes import LadderShapes
from pyladdersim.components import Contact, InvertedContact, OffDelayTimer, OnDelayTimer, Output, PulseTimer
from pyladdersim.ui_models import ComponentView, DiagramView


class LadderVisualizer:
    def __init__(self, ladder):
        self.ladder = ladder
        self.window = tk.Tk()  # Use customtkinter's main window
        self.window.title("Ladder Logic Visualization")

        # Canvas for drawing ladder and rungs
        self.canvas = tk.Canvas(self.window, width=600, height=400, bg="white")
        self.canvas.pack()

        # Bind Q to close the window
        self.window.bind("q", lambda e: self.stop())
        self.window.bind("Q", lambda e: self.stop())

        # Instantiate LadderShapes for drawing components
        self.shapes = LadderShapes(self.canvas)

        # Store references to contact buttons for easy access
        self.contact_buttons = {}

    def toggle_contact(self, contact):
        """Toggle the state of a contact and refresh the visualization."""
        contact.state = not contact.state
        self.update_visualization()

    def update_visualization(self):
        """Updates the ladder visualization to reflect current states."""
        self.canvas.delete("all")
        self.contact_buttons.clear()  # Clear any previous button references

        # Draw the power rails
        self.canvas.create_line(50, 20, 50, 380, fill="black", width=3)
        self.canvas.create_line(550, 20, 550, 380, fill="black", width=3)

        # Define colors for ON/OFF states
        on_color = "#00FF00"  # Bright green
        off_color = "#FF0000"  # Bright red

        for idx, rung in enumerate(self.ladder.rungs):
            y_position = 50 + idx * 70  # Vertical position for each rung

            # Draw the horizontal line for the rung
            rung_color = on_color if rung.evaluate() else off_color
            self.canvas.create_line(50, y_position, 550, y_position, fill=rung_color, width=2)

            # Position components along the rung
            x_position = 100
            for component in rung.components[:-1]:
                
                # Overlay the component on top of the button
                if isinstance(component, OnDelayTimer):
                    self.shapes.draw_timer(x_position, y_position, timer_type="TON", color=on_color if component.Q else off_color)
                elif isinstance(component, OffDelayTimer):
                    self.shapes.draw_timer(x_position, y_position, timer_type="TOF", color=on_color if component.Q else off_color)
                elif isinstance(component, PulseTimer):
                    self.shapes.draw_timer(x_position, y_position, timer_type="TP", color=on_color if component.Q else off_color)
                elif isinstance(component, Contact):
                    self.shapes.draw_contact(x_position, y_position, color=on_color if component.state else off_color)
                    self.canvas.create_text(x_position, y_position - 20, text=component.name, fill=on_color if component.state else off_color)
                elif isinstance(component, InvertedContact):
                    self.shapes.draw_inverted_contact(x_position, y_position, color=on_color if component.state else off_color)
                    self.canvas.create_text(x_position, y_position - 20, text=component.name, fill=on_color if component.state else off_color)
                
                if isinstance(component, Contact) or isinstance(component, InvertedContact):
                    # Create a transparent rectangle with a click event directly on the canvas
                    rect = self.canvas.create_rectangle(x_position - 15, y_position - 10, x_position + 15, y_position + 10,
                                                        outline='', fill='')  # No fill or outline for full transparency
                    self.canvas.tag_bind(rect, "<Button-1>", lambda event, comp=component: self.toggle_contact(comp))
                x_position += 100  # Move x position for the next component

            # Align the output component to the right side
            output_component = rung.components[-1]
            output_color = on_color if output_component.state else off_color
            if isinstance(output_component, Output):
                self.shapes.draw_coil(500, y_position, color=output_color)
                self.canvas.create_text(500, y_position - 20, text=output_component.name, fill=output_color)

        # Update the Tkinter window to reflect changes
        self.window.update_idletasks()
        self.window.update()

    def stop(self):
        """Close the Tkinter window."""
        self.window.destroy()


class TkLadderRenderer:
    """
    Ladder renderer with no simulator dependency.
    It only draws a DiagramView and emits click callbacks by component id.
    """

    def __init__(
        self,
        title: str = "Ladder Diagram",
        width: int = 900,
        height: int = 500,
        on_component_click: Callable[[str], None] | None = None,
    ):
        self.window = tk.Tk()
        self.window.title(title)
        self.canvas = tk.Canvas(self.window, width=width, height=height, bg="white")
        self.canvas.pack(fill="both", expand=True)
        self.shapes = LadderShapes(self.canvas)
        self.on_component_click = on_component_click
        self._closed = False

        self.window.bind("q", lambda _e: self.close())
        self.window.bind("Q", lambda _e: self.close())
        self.window.protocol("WM_DELETE_WINDOW", self.close)

    def render(self, diagram: DiagramView):
        if self._closed:
            return

        self.canvas.delete("all")
        self._draw_rails()

        on_color = "#00AA00"
        off_color = "#CC2222"

        for rung_index, rung in enumerate(diagram.rungs):
            y = 50 + rung_index * 70
            rung_color = on_color if rung.power_on else off_color
            self.canvas.create_line(50, y, 850, y, fill=rung_color, width=2)
            self._draw_rung_components(rung.components, y, on_color, off_color)

        self.window.update_idletasks()
        self.window.update()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.window.destroy()

    def _draw_rails(self):
        self.canvas.create_line(50, 20, 50, 470, fill="black", width=3)
        self.canvas.create_line(850, 20, 850, 470, fill="black", width=3)

    def _draw_rung_components(
        self,
        components: list[ComponentView],
        y: int,
        on_color: str,
        off_color: str,
    ):
        if not components:
            return

        x = 120
        last = len(components) - 1

        for index, component in enumerate(components):
            is_output_position = index == last and component.kind == "coil"
            draw_x = 800 if is_output_position else x
            color = on_color if component.state else off_color

            self._draw_component(component, draw_x, y, color)
            if component.clickable:
                self._bind_click_region(component.id, draw_x, y)

            if not is_output_position:
                x += 120

    def _draw_component(self, component: ComponentView, x: int, y: int, color: str):
        if component.kind == "contact":
            self.shapes.draw_contact(x, y, color=color)
        elif component.kind == "inverted_contact":
            self.shapes.draw_inverted_contact(x, y, color=color)
        elif component.kind == "timer":
            timer_type = component.timer_type or "TMR"
            self.shapes.draw_timer(x, y, timer_type=timer_type, color=color)
        elif component.kind == "coil":
            self.shapes.draw_coil(x, y, color=color)
        else:
            self.canvas.create_rectangle(x - 20, y - 15, x + 20, y + 15, outline=color, width=2)

        self.canvas.create_text(x, y - 22, text=component.name, fill=color)

    def _bind_click_region(self, component_id: str, x: int, y: int):
        if self.on_component_click is None:
            return
        region = self.canvas.create_rectangle(x - 18, y - 12, x + 18, y + 12, outline="", fill="")
        self.canvas.tag_bind(region, "<Button-1>", lambda _e, cid=component_id: self.on_component_click(cid))
