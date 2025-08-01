"""A scene supporting zooming in on a specified section.


Examples
--------

.. manim:: UseZoomedScene

    class UseZoomedScene(ZoomedScene):
        def construct(self):
            dot = Dot().set_color(GREEN)
            self.add(dot)
            self.wait(1)
            self.activate_zooming(animate=False)
            self.wait(1)
            self.play(dot.animate.shift(LEFT))

.. manim:: ChangingZoomScale

    class ChangingZoomScale(ZoomedScene):
        def __init__(self, **kwargs):
            ZoomedScene.__init__(
                self,
                zoom_factor=0.3,
                zoomed_display_height=1,
                zoomed_display_width=3,
                image_frame_stroke_width=20,
                zoomed_camera_config={
                    "default_frame_stroke_width": 3,
                },
                **kwargs
            )

        def construct(self):
            dot = Dot().set_color(GREEN)
            sq = Circle(fill_opacity=1, radius=0.2).next_to(dot, RIGHT)
            self.add(dot, sq)
            self.wait(1)
            self.activate_zooming(animate=False)
            self.wait(1)
            self.play(dot.animate.shift(LEFT * 0.3))

            self.play(self.zoomed_camera.frame.animate.scale(4))
            self.play(self.zoomed_camera.frame.animate.shift(0.5 * DOWN))

"""

from __future__ import annotations

__all__ = ["ZoomedScene"]

from typing import TYPE_CHECKING, Any

from ..animation.transform import ApplyMethod
from ..camera.camera import Camera
from ..camera.moving_camera import MovingCamera
from ..camera.multi_camera import MultiCamera
from ..constants import *
from ..mobject.types.image_mobject import ImageMobjectFromCamera
from ..renderer.opengl_renderer import OpenGLCamera
from ..scene.moving_camera_scene import MovingCameraScene

if TYPE_CHECKING:
    from manim.typing import Point3DLike, Vector3D

# Note, any scenes from old videos using ZoomedScene will almost certainly
# break, as it was restructured.


class ZoomedScene(MovingCameraScene):
    """This is a Scene with special configurations made for when
    a particular part of the scene must be zoomed in on and displayed
    separately.
    """

    def __init__(
        self,
        camera_class: type[Camera] = MultiCamera,
        zoomed_display_height: float = 3,
        zoomed_display_width: float = 3,
        zoomed_display_center: Point3DLike | None = None,
        zoomed_display_corner: Vector3D = UP + RIGHT,
        zoomed_display_corner_buff: float = DEFAULT_MOBJECT_TO_EDGE_BUFFER,
        zoomed_camera_config: dict[str, Any] = {
            "default_frame_stroke_width": 2,
            "background_opacity": 1,
        },
        zoomed_camera_image_mobject_config: dict[str, Any] = {},
        zoomed_camera_frame_starting_position: Point3DLike = ORIGIN,
        zoom_factor: float = 0.15,
        image_frame_stroke_width: float = 3,
        zoom_activated: bool = False,
        **kwargs: Any,
    ) -> None:
        self.zoomed_display_height = zoomed_display_height
        self.zoomed_display_width = zoomed_display_width
        self.zoomed_display_center = zoomed_display_center
        self.zoomed_display_corner = zoomed_display_corner
        self.zoomed_display_corner_buff = zoomed_display_corner_buff
        self.zoomed_camera_config = zoomed_camera_config
        self.zoomed_camera_image_mobject_config = zoomed_camera_image_mobject_config
        self.zoomed_camera_frame_starting_position = (
            zoomed_camera_frame_starting_position
        )
        self.zoom_factor = zoom_factor
        self.image_frame_stroke_width = image_frame_stroke_width
        self.zoom_activated = zoom_activated
        super().__init__(camera_class=camera_class, **kwargs)

    def setup(self) -> None:
        """This method is used internally by Manim to
        setup the scene for proper use.
        """
        super().setup()
        # Initialize camera and display
        zoomed_camera = MovingCamera(**self.zoomed_camera_config)
        zoomed_display = ImageMobjectFromCamera(
            zoomed_camera, **self.zoomed_camera_image_mobject_config
        )
        zoomed_display.add_display_frame()
        for mob in zoomed_camera.frame, zoomed_display:
            mob.stretch_to_fit_height(self.zoomed_display_height)
            mob.stretch_to_fit_width(self.zoomed_display_width)
        zoomed_camera.frame.scale(self.zoom_factor)

        # Position camera and display
        zoomed_camera.frame.move_to(self.zoomed_camera_frame_starting_position)
        if self.zoomed_display_center is not None:
            zoomed_display.move_to(self.zoomed_display_center)
        else:
            zoomed_display.to_corner(
                self.zoomed_display_corner,
                buff=self.zoomed_display_corner_buff,
            )

        self.zoomed_camera = zoomed_camera
        self.zoomed_display = zoomed_display

    def activate_zooming(self, animate: bool = False) -> None:
        """This method is used to activate the zooming for the zoomed_camera.

        Parameters
        ----------
        animate
            Whether or not to animate the activation
            of the zoomed camera.
        """
        self.zoom_activated = True
        self.renderer.camera.add_image_mobject_from_camera(self.zoomed_display)  # type: ignore[union-attr]
        if animate:
            self.play(self.get_zoom_in_animation())
            self.play(self.get_zoomed_display_pop_out_animation())
        self.add_foreground_mobjects(
            self.zoomed_camera.frame,
            self.zoomed_display,
        )

    def get_zoom_in_animation(self, run_time: float = 2, **kwargs: Any) -> ApplyMethod:
        """Returns the animation of camera zooming in.

        Parameters
        ----------
        run_time
            The run_time of the animation of the camera zooming in.
        **kwargs
            Any valid keyword arguments of ApplyMethod()

        Returns
        -------
        ApplyMethod
            The animation of the camera zooming in.
        """
        frame = self.zoomed_camera.frame
        if isinstance(self.camera, OpenGLCamera):
            full_frame_width, full_frame_height = self.camera.frame_shape
        else:
            full_frame_height = self.camera.frame_height
            full_frame_width = self.camera.frame_width
        frame.save_state()
        frame.stretch_to_fit_width(full_frame_width)
        frame.stretch_to_fit_height(full_frame_height)
        frame.center()
        frame.set_stroke(width=0)
        return ApplyMethod(frame.restore, run_time=run_time, **kwargs)

    def get_zoomed_display_pop_out_animation(self, **kwargs: Any) -> ApplyMethod:
        """This is the animation of the popping out of the mini-display that
        shows the content of the zoomed camera.

        Returns
        -------
        ApplyMethod
            The Animation of the Zoomed Display popping out.
        """
        display = self.zoomed_display
        display.save_state()
        display.replace(self.zoomed_camera.frame, stretch=True)
        return ApplyMethod(display.restore)

    def get_zoom_factor(self) -> float:
        """Returns the Zoom factor of the Zoomed camera.

        Defined as the ratio between the height of the zoomed camera and
        the height of the zoomed mini display.

        Returns
        -------
        float
            The zoom factor.
        """
        zoom_factor: float = (
            self.zoomed_camera.frame.height / self.zoomed_display.height
        )
        return zoom_factor
