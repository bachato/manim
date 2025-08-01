"""Mobjects that use vector graphics."""

from __future__ import annotations

__all__ = [
    "VMobject",
    "VGroup",
    "VDict",
    "VectorizedPoint",
    "CurvesAsSubmobjects",
    "DashedVMobject",
]

import itertools as it
import sys
from collections.abc import Hashable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Callable, Literal

import numpy as np
from PIL.Image import Image

from manim import config
from manim.constants import *
from manim.mobject.mobject import Mobject
from manim.mobject.opengl.opengl_compatibility import ConvertToOpenGL
from manim.mobject.opengl.opengl_mobject import OpenGLMobject
from manim.mobject.opengl.opengl_vectorized_mobject import OpenGLVMobject
from manim.mobject.three_d.three_d_utils import (
    get_3d_vmob_gradient_start_and_end_points,
)
from manim.utils.bezier import (
    bezier,
    bezier_remap,
    get_smooth_cubic_bezier_handle_points,
    integer_interpolate,
    interpolate,
    partial_bezier_points,
    proportions_along_bezier_curve_for_point,
)
from manim.utils.color import BLACK, WHITE, ManimColor, ParsableManimColor
from manim.utils.iterables import (
    make_even,
    resize_array,
    stretch_array_to_length,
    tuplify,
)
from manim.utils.space_ops import rotate_vector, shoelace_direction

if TYPE_CHECKING:
    from typing import Any

    import numpy.typing as npt
    from typing_extensions import Self

    from manim.typing import (
        CubicBezierPath,
        CubicBezierPointsLike,
        CubicSpline,
        ManimFloat,
        MappingFunction,
        Point2DLike,
        Point3D,
        Point3D_Array,
        Point3DLike,
        Point3DLike_Array,
        RGBA_Array_Float,
        Vector3D,
        Vector3DLike,
        Zeros,
    )

# TODO
# - Change cubic curve groups to have 4 points instead of 3
# - Change sub_path idea accordingly
# - No more mark_paths_closed, instead have the camera test
#   if last point in close to first point
# - Think about length of self.points.  Always 0 or 1 mod 4?
#   That's kind of weird.


class VMobject(Mobject):
    """A vectorized mobject.

    Parameters
    ----------
    background_stroke_color
        The purpose of background stroke is to have something
        that won't overlap fill, e.g.  For text against some
        textured background.
    sheen_factor
        When a color c is set, there will be a second color
        computed based on interpolating c to WHITE by with
        sheen_factor, and the display will gradient to this
        secondary color in the direction of sheen_direction.
    close_new_points
        Indicates that it will not be displayed, but
        that it should count in parent mobject's path
    tolerance_for_point_equality
        This is within a pixel
    joint_type
        The line joint type used to connect the curve segments
        of this vectorized mobject. See :class:`.LineJointType`
        for options.
    """

    sheen_factor = 0.0

    def __init__(
        self,
        fill_color: ParsableManimColor | None = None,
        fill_opacity: float = 0.0,
        stroke_color: ParsableManimColor | None = None,
        stroke_opacity: float = 1.0,
        stroke_width: float = DEFAULT_STROKE_WIDTH,
        background_stroke_color: ParsableManimColor | None = BLACK,
        background_stroke_opacity: float = 1.0,
        background_stroke_width: float = 0,
        sheen_factor: float = 0.0,
        joint_type: LineJointType | None = None,
        sheen_direction: Vector3DLike = UL,
        close_new_points: bool = False,
        pre_function_handle_to_anchor_scale_factor: float = 0.01,
        make_smooth_after_applying_functions: bool = False,
        background_image: Image | str | None = None,
        shade_in_3d: bool = False,
        # TODO, do we care about accounting for varying zoom levels?
        tolerance_for_point_equality: float = 1e-6,
        n_points_per_cubic_curve: int = 4,
        cap_style: CapStyleType = CapStyleType.AUTO,
        **kwargs: Any,
    ):
        self.fill_opacity = fill_opacity
        self.stroke_opacity = stroke_opacity
        self.stroke_width = stroke_width
        if background_stroke_color is not None:
            self.background_stroke_color: ManimColor = ManimColor(
                background_stroke_color
            )
        self.background_stroke_opacity: float = background_stroke_opacity
        self.background_stroke_width: float = background_stroke_width
        self.sheen_factor: float = sheen_factor
        self.joint_type: LineJointType = (
            LineJointType.AUTO if joint_type is None else joint_type
        )
        self.sheen_direction = sheen_direction
        self.close_new_points: bool = close_new_points
        self.pre_function_handle_to_anchor_scale_factor: float = (
            pre_function_handle_to_anchor_scale_factor
        )
        self.make_smooth_after_applying_functions: bool = (
            make_smooth_after_applying_functions
        )
        self.background_image: Image | str | None = background_image
        self.shade_in_3d: bool = shade_in_3d
        self.tolerance_for_point_equality: float = tolerance_for_point_equality
        self.n_points_per_cubic_curve: int = n_points_per_cubic_curve
        self._bezier_t_values: npt.NDArray[float] = np.linspace(
            0, 1, n_points_per_cubic_curve
        )
        self.cap_style: CapStyleType = cap_style
        super().__init__(**kwargs)
        self.submobjects: list[VMobject]

        # TODO: Find where color overwrites are happening and remove the color doubling
        # if "color" in kwargs:
        #     fill_color = kwargs["color"]
        #     stroke_color = kwargs["color"]
        if fill_color is not None:
            self.fill_color = ManimColor.parse(fill_color)
        if stroke_color is not None:
            self.stroke_color = ManimColor.parse(stroke_color)

    def _assert_valid_submobjects(self, submobjects: Iterable[VMobject]) -> Self:
        return self._assert_valid_submobjects_internal(submobjects, VMobject)

    # OpenGL compatibility
    @property
    def n_points_per_curve(self) -> int:
        return self.n_points_per_cubic_curve

    def get_group_class(self) -> type[VGroup]:
        return VGroup

    @staticmethod
    def get_mobject_type_class() -> type[VMobject]:
        return VMobject

    # Colors
    def init_colors(self, propagate_colors: bool = True) -> Self:
        self.set_fill(
            color=self.fill_color,
            opacity=self.fill_opacity,
            family=propagate_colors,
        )
        self.set_stroke(
            color=self.stroke_color,
            width=self.stroke_width,
            opacity=self.stroke_opacity,
            family=propagate_colors,
        )
        self.set_background_stroke(
            color=self.background_stroke_color,
            width=self.background_stroke_width,
            opacity=self.background_stroke_opacity,
            family=propagate_colors,
        )
        self.set_sheen(
            factor=self.sheen_factor,
            direction=self.sheen_direction,
            family=propagate_colors,
        )

        if not propagate_colors:
            for submobject in self.submobjects:
                submobject.init_colors(propagate_colors=False)

        return self

    def generate_rgbas_array(
        self, color: ManimColor | list[ManimColor], opacity: float | Iterable[float]
    ) -> RGBA_Array_Float:
        """
        First arg can be either a color, or a tuple/list of colors.
        Likewise, opacity can either be a float, or a tuple of floats.
        If self.sheen_factor is not zero, and only
        one color was passed in, a second slightly light color
        will automatically be added for the gradient
        """
        colors: list[ManimColor] = [
            ManimColor(c) if (c is not None) else BLACK for c in tuplify(color)
        ]
        opacities: list[float] = [
            o if (o is not None) else 0.0 for o in tuplify(opacity)
        ]
        rgbas: npt.NDArray[RGBA_Array_Float] = np.array(
            [c.to_rgba_with_alpha(o) for c, o in zip(*make_even(colors, opacities))],
        )

        sheen_factor = self.get_sheen_factor()
        if sheen_factor != 0 and len(rgbas) == 1:
            light_rgbas = np.array(rgbas)
            light_rgbas[:, :3] += sheen_factor
            np.clip(light_rgbas, 0, 1, out=light_rgbas)
            rgbas = np.append(rgbas, light_rgbas, axis=0)
        return rgbas

    def update_rgbas_array(
        self,
        array_name: str,
        color: ManimColor | None = None,
        opacity: float | None = None,
    ) -> Self:
        rgbas = self.generate_rgbas_array(color, opacity)
        if not hasattr(self, array_name):
            setattr(self, array_name, rgbas)
            return self
        # Match up current rgbas array with the newly calculated
        # one. 99% of the time they'll be the same.
        curr_rgbas = getattr(self, array_name)
        if len(curr_rgbas) < len(rgbas):
            curr_rgbas = stretch_array_to_length(curr_rgbas, len(rgbas))
            setattr(self, array_name, curr_rgbas)
        elif len(rgbas) < len(curr_rgbas):
            rgbas = stretch_array_to_length(rgbas, len(curr_rgbas))
        # Only update rgb if color was not None, and only
        # update alpha channel if opacity was passed in
        if color is not None:
            curr_rgbas[:, :3] = rgbas[:, :3]
        if opacity is not None:
            curr_rgbas[:, 3] = rgbas[:, 3]
        return self

    def set_fill(
        self,
        color: ParsableManimColor | None = None,
        opacity: float | None = None,
        family: bool = True,
    ) -> Self:
        """Set the fill color and fill opacity of a :class:`VMobject`.

        Parameters
        ----------
        color
            Fill color of the :class:`VMobject`.
        opacity
            Fill opacity of the :class:`VMobject`.
        family
            If ``True``, the fill color of all submobjects is also set.

        Returns
        -------
        :class:`VMobject`
            ``self``

        Examples
        --------
        .. manim:: SetFill
            :save_last_frame:

            class SetFill(Scene):
                def construct(self):
                    square = Square().scale(2).set_fill(WHITE,1)
                    circle1 = Circle().set_fill(GREEN,0.8)
                    circle2 = Circle().set_fill(YELLOW) # No fill_opacity
                    circle3 = Circle().set_fill(color = '#FF2135', opacity = 0.2)
                    group = Group(circle1,circle2,circle3).arrange()
                    self.add(square)
                    self.add(group)

        See Also
        --------
        :meth:`~.VMobject.set_style`
        """
        if family:
            for submobject in self.submobjects:
                submobject.set_fill(color, opacity, family)
        self.update_rgbas_array("fill_rgbas", color, opacity)
        self.fill_rgbas: RGBA_Array_Float
        if opacity is not None:
            self.fill_opacity = opacity
        return self

    def set_stroke(
        self,
        color: ParsableManimColor = None,
        width: float | None = None,
        opacity: float | None = None,
        background=False,
        family: bool = True,
    ) -> Self:
        if family:
            for submobject in self.submobjects:
                submobject.set_stroke(color, width, opacity, background, family)
        if background:
            array_name = "background_stroke_rgbas"
            width_name = "background_stroke_width"
            opacity_name = "background_stroke_opacity"
        else:
            array_name = "stroke_rgbas"
            width_name = "stroke_width"
            opacity_name = "stroke_opacity"
        self.update_rgbas_array(array_name, color, opacity)
        if width is not None:
            setattr(self, width_name, width)
        if opacity is not None:
            setattr(self, opacity_name, opacity)
        if color is not None and background:
            if isinstance(color, (list, tuple)):
                self.background_stroke_color = ManimColor.parse(color)
            else:
                self.background_stroke_color = ManimColor(color)
        return self

    def set_cap_style(self, cap_style: CapStyleType) -> Self:
        """
        Sets the cap style of the :class:`VMobject`.

        Parameters
        ----------
        cap_style
            The cap style to be set. See :class:`.CapStyleType` for options.

        Returns
        -------
        :class:`VMobject`
            ``self``

        Examples
        --------
        .. manim:: CapStyleExample
            :save_last_frame:

            class CapStyleExample(Scene):
                def construct(self):
                    line = Line(LEFT, RIGHT, color=YELLOW, stroke_width=20)
                    line.set_cap_style(CapStyleType.ROUND)
                    self.add(line)
        """
        self.cap_style = cap_style
        return self

    def set_background_stroke(self, **kwargs) -> Self:
        kwargs["background"] = True
        self.set_stroke(**kwargs)
        return self

    def set_style(
        self,
        fill_color: ParsableManimColor | None = None,
        fill_opacity: float | None = None,
        stroke_color: ParsableManimColor | None = None,
        stroke_width: float | None = None,
        stroke_opacity: float | None = None,
        background_stroke_color: ParsableManimColor | None = None,
        background_stroke_width: float | None = None,
        background_stroke_opacity: float | None = None,
        sheen_factor: float | None = None,
        sheen_direction: Vector3DLike | None = None,
        background_image: Image | str | None = None,
        family: bool = True,
    ) -> Self:
        self.set_fill(color=fill_color, opacity=fill_opacity, family=family)
        self.set_stroke(
            color=stroke_color,
            width=stroke_width,
            opacity=stroke_opacity,
            family=family,
        )
        self.set_background_stroke(
            color=background_stroke_color,
            width=background_stroke_width,
            opacity=background_stroke_opacity,
            family=family,
        )
        if sheen_factor:
            self.set_sheen(
                factor=sheen_factor,
                direction=sheen_direction,
                family=family,
            )
        if background_image:
            self.color_using_background_image(background_image)
        return self

    def get_style(self, simple: bool = False) -> dict:
        ret = {
            "stroke_opacity": self.get_stroke_opacity(),
            "stroke_width": self.get_stroke_width(),
        }

        # TODO: FIX COLORS HERE
        if simple:
            ret["fill_color"] = self.get_fill_color()
            ret["fill_opacity"] = self.get_fill_opacity()
            ret["stroke_color"] = self.get_stroke_color()
        else:
            ret["fill_color"] = self.get_fill_colors()
            ret["fill_opacity"] = self.get_fill_opacities()
            ret["stroke_color"] = self.get_stroke_colors()
            ret["background_stroke_color"] = self.get_stroke_colors(background=True)
            ret["background_stroke_width"] = self.get_stroke_width(background=True)
            ret["background_stroke_opacity"] = self.get_stroke_opacity(background=True)
            ret["sheen_factor"] = self.get_sheen_factor()
            ret["sheen_direction"] = self.get_sheen_direction()
            ret["background_image"] = self.get_background_image()

        return ret

    def match_style(self, vmobject: VMobject, family: bool = True) -> Self:
        self.set_style(**vmobject.get_style(), family=False)

        if family:
            # Does its best to match up submobject lists, and
            # match styles accordingly
            submobs1, submobs2 = self.submobjects, vmobject.submobjects
            if len(submobs1) == 0:
                return self
            elif len(submobs2) == 0:
                submobs2 = [vmobject]
            for sm1, sm2 in zip(*make_even(submobs1, submobs2)):
                sm1.match_style(sm2)
        return self

    def set_color(self, color: ParsableManimColor, family: bool = True) -> Self:
        self.set_fill(color, family=family)
        self.set_stroke(color, family=family)
        return self

    def set_opacity(self, opacity: float, family: bool = True) -> Self:
        self.set_fill(opacity=opacity, family=family)
        self.set_stroke(opacity=opacity, family=family)
        self.set_stroke(opacity=opacity, family=family, background=True)
        return self

    def scale(self, scale_factor: float, scale_stroke: bool = False, **kwargs) -> Self:
        r"""Scale the size by a factor.

        Default behavior is to scale about the center of the vmobject.

        Parameters
        ----------
        scale_factor
            The scaling factor :math:`\alpha`. If :math:`0 < |\alpha| < 1`, the mobject
            will shrink, and for :math:`|\alpha| > 1` it will grow. Furthermore,
            if :math:`\alpha < 0`, the mobject is also flipped.
        scale_stroke
            Boolean determining if the object's outline is scaled when the object is scaled.
            If enabled, and object with 2px outline is scaled by a factor of .5, it will have an outline of 1px.
        kwargs
            Additional keyword arguments passed to
            :meth:`~.Mobject.scale`.

        Returns
        -------
        :class:`VMobject`
            ``self``

        Examples
        --------

        .. manim:: MobjectScaleExample
            :save_last_frame:

            class MobjectScaleExample(Scene):
                def construct(self):
                    c1 = Circle(1, RED).set_x(-1)
                    c2 = Circle(1, GREEN).set_x(1)

                    vg = VGroup(c1, c2)
                    vg.set_stroke(width=50)
                    self.add(vg)

                    self.play(
                        c1.animate.scale(.25),
                        c2.animate.scale(.25,
                            scale_stroke=True)
                    )

        See also
        --------
        :meth:`move_to`

        """
        if scale_stroke:
            self.set_stroke(width=abs(scale_factor) * self.get_stroke_width())
            self.set_stroke(
                width=abs(scale_factor) * self.get_stroke_width(background=True),
                background=True,
            )
        super().scale(scale_factor, **kwargs)
        return self

    def fade(self, darkness: float = 0.5, family: bool = True) -> Self:
        factor = 1.0 - darkness
        self.set_fill(opacity=factor * self.get_fill_opacity(), family=False)
        self.set_stroke(opacity=factor * self.get_stroke_opacity(), family=False)
        self.set_background_stroke(
            opacity=factor * self.get_stroke_opacity(background=True),
            family=False,
        )
        super().fade(darkness, family)
        return self

    def get_fill_rgbas(self) -> RGBA_Array_Float | Zeros:
        try:
            return self.fill_rgbas
        except AttributeError:
            return np.zeros((1, 4))

    def get_fill_color(self) -> ManimColor:
        """
        If there are multiple colors (for gradient)
        this returns the first one
        """
        return self.get_fill_colors()[0]

    fill_color = property(get_fill_color, set_fill)

    def get_fill_opacity(self) -> ManimFloat:
        """
        If there are multiple opacities, this returns the
        first
        """
        return self.get_fill_opacities()[0]

    # TODO: Does this just do a copy?
    # TODO: I have the feeling that this function should not return None, does that have any usage ?
    def get_fill_colors(self) -> list[ManimColor | None]:
        return [
            ManimColor(rgba[:3]) if rgba.any() else None
            for rgba in self.get_fill_rgbas()
        ]

    def get_fill_opacities(self) -> npt.NDArray[ManimFloat]:
        return self.get_fill_rgbas()[:, 3]

    def get_stroke_rgbas(self, background: bool = False) -> RGBA_Array_float | Zeros:
        try:
            if background:
                self.background_stroke_rgbas: RGBA_Array_Float
                rgbas = self.background_stroke_rgbas
            else:
                self.stroke_rgbas: RGBA_Array_Float
                rgbas = self.stroke_rgbas
            return rgbas
        except AttributeError:
            return np.zeros((1, 4))

    def get_stroke_color(self, background: bool = False) -> ManimColor | None:
        return self.get_stroke_colors(background)[0]

    stroke_color = property(get_stroke_color, set_stroke)

    def get_stroke_width(self, background: bool = False) -> float:
        if background:
            self.background_stroke_width: float
            width = self.background_stroke_width
        else:
            width = self.stroke_width
            if isinstance(width, str):
                width = int(width)
        return max(0.0, width)

    def get_stroke_opacity(self, background: bool = False) -> ManimFloat:
        return self.get_stroke_opacities(background)[0]

    def get_stroke_colors(self, background: bool = False) -> list[ManimColor | None]:
        return [
            ManimColor(rgba[:3]) if rgba.any() else None
            for rgba in self.get_stroke_rgbas(background)
        ]

    def get_stroke_opacities(self, background: bool = False) -> npt.NDArray[ManimFloat]:
        return self.get_stroke_rgbas(background)[:, 3]

    def get_color(self) -> ManimColor:
        if np.all(self.get_fill_opacities() == 0):
            return self.get_stroke_color()
        return self.get_fill_color()

    color = property(get_color, set_color)

    def set_sheen_direction(self, direction: Vector3DLike, family: bool = True) -> Self:
        """Sets the direction of the applied sheen.

        Parameters
        ----------
        direction
            Direction from where the gradient is applied.

        Examples
        --------
        Normal usage::

            Circle().set_sheen_direction(UP)

        See Also
        --------
        :meth:`~.VMobject.set_sheen`
        :meth:`~.VMobject.rotate_sheen_direction`
        """
        direction_copy = np.array(direction)
        if family:
            for submob in self.get_family():
                submob.sheen_direction = direction_copy.copy()
        else:
            self.sheen_direction = direction_copy
        return self

    def rotate_sheen_direction(
        self, angle: float, axis: Vector3DLike = OUT, family: bool = True
    ) -> Self:
        """Rotates the direction of the applied sheen.

        Parameters
        ----------
        angle
            Angle by which the direction of sheen is rotated.
        axis
            Axis of rotation.

        Examples
        --------
        Normal usage::

            Circle().set_sheen_direction(UP).rotate_sheen_direction(PI)

        See Also
        --------
        :meth:`~.VMobject.set_sheen_direction`
        """
        if family:
            for submob in self.get_family():
                submob.sheen_direction = rotate_vector(
                    submob.sheen_direction,
                    angle,
                    axis,
                )
        else:
            self.sheen_direction = rotate_vector(self.sheen_direction, angle, axis)
        return self

    def set_sheen(
        self, factor: float, direction: Vector3DLike | None = None, family: bool = True
    ) -> Self:
        """Applies a color gradient from a direction.

        Parameters
        ----------
        factor
            The extent of lustre/gradient to apply. If negative, the gradient
            starts from black, if positive the gradient starts from white and
            changes to the current color.
        direction
            Direction from where the gradient is applied.

        Examples
        --------
        .. manim:: SetSheen
            :save_last_frame:

            class SetSheen(Scene):
                def construct(self):
                    circle = Circle(fill_opacity=1).set_sheen(-0.3, DR)
                    self.add(circle)
        """
        if family:
            for submob in self.submobjects:
                submob.set_sheen(factor, direction, family)
        self.sheen_factor: float = factor
        if direction is not None:
            # family set to false because recursion will
            # already be handled above
            self.set_sheen_direction(direction, family=False)
        # Reset color to put sheen_factor into effect
        if factor != 0:
            self.set_stroke(self.get_stroke_color(), family=family)
            self.set_fill(self.get_fill_color(), family=family)
        return self

    def get_sheen_direction(self) -> Vector3D:
        return np.array(self.sheen_direction)

    def get_sheen_factor(self) -> float:
        return self.sheen_factor

    def get_gradient_start_and_end_points(self) -> tuple[Point3D, Point3D]:
        if self.shade_in_3d:
            return get_3d_vmob_gradient_start_and_end_points(self)
        else:
            direction = self.get_sheen_direction()
            c = self.get_center()
            bases = np.array(
                [self.get_edge_center(vect) - c for vect in [RIGHT, UP, OUT]],
            ).transpose()
            offset = np.dot(bases, direction)
            return (c - offset, c + offset)

    def color_using_background_image(self, background_image: Image | str) -> Self:
        self.background_image: Image | str = background_image
        self.set_color(WHITE)
        for submob in self.submobjects:
            submob.color_using_background_image(background_image)
        return self

    def get_background_image(self) -> Image | str:
        return self.background_image

    def match_background_image(self, vmobject: VMobject) -> Self:
        self.color_using_background_image(vmobject.get_background_image())
        return self

    def set_shade_in_3d(
        self, value: bool = True, z_index_as_group: bool = False
    ) -> Self:
        for submob in self.get_family():
            submob.shade_in_3d = value
            if z_index_as_group:
                submob.z_index_group = self
        return self

    def set_points(self, points: Point3DLike_Array) -> Self:
        self.points: Point3D_Array = np.array(points)
        return self

    def resize_points(
        self,
        new_length: int,
        resize_func: Callable[[Point3D_Array, int], Point3D_Array] = resize_array,
    ) -> Self:
        """Resize the array of anchor points and handles to have
        the specified size.

        Parameters
        ----------
        new_length
            The new (total) number of points.
        resize_func
            A function mapping a Numpy array (the points) and an integer
            (the target size) to a Numpy array. The default implementation
            is based on Numpy's ``resize`` function.
        """
        if new_length != len(self.points):
            self.points = resize_func(self.points, new_length)
        return self

    def set_anchors_and_handles(
        self,
        anchors1: Point3DLike_Array,
        handles1: Point3DLike_Array,
        handles2: Point3DLike_Array,
        anchors2: Point3DLike_Array,
    ) -> Self:
        """Given two sets of anchors and handles, process them to set them as anchors
        and handles of the VMobject.

        anchors1[i], handles1[i], handles2[i] and anchors2[i] define the i-th bezier
        curve of the vmobject. There are four hardcoded parameters and this is a
        problem as it makes the number of points per cubic curve unchangeable from 4
        (two anchors and two handles).

        Returns
        -------
        :class:`VMobject`
            ``self``
        """
        assert len(anchors1) == len(handles1) == len(handles2) == len(anchors2)
        nppcc = self.n_points_per_cubic_curve  # 4
        total_len = nppcc * len(anchors1)
        self.points = np.empty((total_len, self.dim))
        # the following will, from the four sets, dispatch them in points such that
        # self.points = [
        #     anchors1[0], handles1[0], handles2[0], anchors1[0], anchors1[1],
        #     handles1[1], ...
        # ]
        arrays = [anchors1, handles1, handles2, anchors2]
        for index, array in enumerate(arrays):
            self.points[index::nppcc] = array
        return self

    def clear_points(self) -> None:
        # TODO: shouldn't this return self instead of None?
        self.points = np.zeros((0, self.dim))

    def append_points(self, new_points: Point3DLike_Array) -> Self:
        """Append the given ``new_points`` to the end of
        :attr:`VMobject.points`.

        Parameters
        ----------
        new_points
            An array of 3D points to append.

        Returns
        -------
        :class:`VMobject`
            The VMobject itself, after appending ``new_points``.
        """
        # TODO, check that number new points is a multiple of 4?
        # or else that if len(self.points) % 4 == 1, then
        # len(new_points) % 4 == 3?
        n = len(self.points)
        points = np.empty((n + len(new_points), self.dim))
        points[:n] = self.points
        points[n:] = new_points
        self.points = points
        return self

    def start_new_path(self, point: Point3DLike) -> Self:
        """Append a ``point`` to the :attr:`VMobject.points`, which will be the
        beginning of a new Bézier curve in the path given by the points. If
        there's an unfinished curve at the end of :attr:`VMobject.points`,
        complete it by appending the last Bézier curve's start anchor as many
        times as needed.

        Parameters
        ----------
        point
            A 3D point to append to :attr:`VMobject.points`.

        Returns
        -------
        :class:`VMobject`
            The VMobject itself, after appending ``point`` and starting a new
            curve.
        """
        n_points = len(self.points)
        nppc = self.n_points_per_curve
        if n_points % nppc != 0:
            # close the open path by appending the last
            # start anchor sufficiently often
            last_anchor = self.get_start_anchors()[-1]
            closure = [last_anchor] * (nppc - (n_points % nppc))
            self.append_points(closure + [point])
        else:
            self.append_points([point])
        return self

    def add_cubic_bezier_curve(
        self,
        anchor1: Point3DLike,
        handle1: Point3DLike,
        handle2: Point3DLike,
        anchor2: Point3DLike,
    ) -> None:
        # TODO, check the len(self.points) % 4 == 0?
        self.append_points([anchor1, handle1, handle2, anchor2])

    # what type is curves?
    def add_cubic_bezier_curves(self, curves) -> None:
        self.append_points(curves.flatten())

    def add_cubic_bezier_curve_to(
        self,
        handle1: Point3DLike,
        handle2: Point3DLike,
        anchor: Point3DLike,
    ) -> Self:
        """Add cubic bezier curve to the path.

        NOTE : the first anchor is not a parameter as by default the end of the last sub-path!

        Parameters
        ----------
        handle1
            first handle
        handle2
            second handle
        anchor
            anchor

        Returns
        -------
        :class:`VMobject`
            ``self``
        """
        self.throw_error_if_no_points()
        new_points = [handle1, handle2, anchor]
        if self.has_new_path_started():
            self.append_points(new_points)
        else:
            self.append_points([self.get_last_point()] + new_points)
        return self

    def add_quadratic_bezier_curve_to(
        self,
        handle: Point3DLike,
        anchor: Point3DLike,
    ) -> Self:
        """Add Quadratic bezier curve to the path.

        Returns
        -------
        :class:`VMobject`
            ``self``
        """
        # How does one approximate a quadratic with a cubic?
        # refer to the Wikipedia page on Bezier curves
        # https://en.wikipedia.org/wiki/B%C3%A9zier_curve#Degree_elevation, accessed Jan 20, 2021
        # 1. Copy the end points, and then
        # 2. Place the 2 middle control points 2/3 along the line segments
        # from the end points to the quadratic curve's middle control point.
        # I think that's beautiful.
        self.add_cubic_bezier_curve_to(
            2 / 3 * handle + 1 / 3 * self.get_last_point(),
            2 / 3 * handle + 1 / 3 * anchor,
            anchor,
        )
        return self

    def add_line_to(self, point: Point3DLike) -> Self:
        """Add a straight line from the last point of VMobject to the given point.

        Parameters
        ----------

        point
            The end of the straight line.

        Returns
        -------
        :class:`VMobject`
            ``self``
        """
        self.add_cubic_bezier_curve_to(
            *(
                interpolate(self.get_last_point(), point, t)
                for t in self._bezier_t_values[1:]
            )
        )
        return self

    def add_smooth_curve_to(self, *points: Point3DLike) -> Self:
        """Creates a smooth curve from given points and add it to the VMobject. If two points are passed in, the first is interpreted
        as a handle, the second as an anchor.

        Parameters
        ----------
        points
            Points (anchor and handle, or just anchor) to add a smooth curve from

        Returns
        -------
        :class:`VMobject`
            ``self``

        Raises
        ------
        ValueError
            If 0 or more than 2 points are given.
        """
        # TODO remove the value error and just add two parameters with one optional
        if len(points) == 1:
            handle2 = None
            new_anchor = points[0]
        elif len(points) == 2:
            handle2, new_anchor = points
        else:
            name = sys._getframe(0).f_code.co_name
            raise ValueError(f"Only call {name} with 1 or 2 points")

        if self.has_new_path_started():
            self.add_line_to(new_anchor)
        else:
            self.throw_error_if_no_points()
            last_h2, last_a2 = self.points[-2:]
            last_tangent = last_a2 - last_h2
            handle1 = last_a2 + last_tangent
            if handle2 is None:
                to_anchor_vect = new_anchor - last_a2
                new_tangent = rotate_vector(last_tangent, PI, axis=to_anchor_vect)
                handle2 = new_anchor - new_tangent
            self.append_points([last_a2, handle1, handle2, new_anchor])
        return self

    def has_new_path_started(self) -> bool:
        nppcc = self.n_points_per_cubic_curve  # 4
        # A new path starting is defined by a control point which is not part of a bezier subcurve.
        return len(self.points) % nppcc == 1

    def get_last_point(self) -> Point3D:
        return self.points[-1]

    def is_closed(self) -> bool:
        # TODO use consider_points_equals_2d ?
        return self.consider_points_equals(self.points[0], self.points[-1])

    def close_path(self) -> None:
        if not self.is_closed():
            self.add_line_to(self.get_subpaths()[-1][0])

    def add_points_as_corners(self, points: Point3DLike_Array) -> Self:
        """Append multiple straight lines at the end of
        :attr:`VMobject.points`, which connect the given ``points`` in order
        starting from the end of the current path. These ``points`` would be
        therefore the corners of the new polyline appended to the path.

        Parameters
        ----------
        points
            An array of 3D points representing the corners of the polyline to
            append to :attr:`VMobject.points`.

        Returns
        -------
        :class:`VMobject`
            The VMobject itself, after appending the straight lines to its
            path.
        """
        self.throw_error_if_no_points()

        points = np.asarray(points).reshape(-1, self.dim)
        num_points = points.shape[0]
        if num_points == 0:
            return self

        start_corners = np.empty((num_points, self.dim))
        start_corners[0] = self.points[-1]
        start_corners[1:] = points[:-1]
        end_corners = points

        if self.has_new_path_started():
            # Remove the last point from the new path
            self.points = self.points[:-1]

        nppcc = self.n_points_per_cubic_curve
        new_points = np.empty((nppcc * start_corners.shape[0], self.dim))
        new_points[::nppcc] = start_corners
        new_points[nppcc - 1 :: nppcc] = end_corners
        for i, t in enumerate(self._bezier_t_values):
            new_points[i::nppcc] = interpolate(start_corners, end_corners, t)

        self.append_points(new_points)
        return self

    def set_points_as_corners(self, points: Point3DLike_Array) -> Self:
        """Given an array of points, set them as corners of the
        :class:`VMobject`.

        To achieve that, this algorithm sets handles aligned with the anchors
        such that the resultant Bézier curve will be the segment between the
        two anchors.

        Parameters
        ----------
        points
            Array of points that will be set as corners.

        Returns
        -------
        :class:`VMobject`
            The VMobject itself, after setting the new points as corners.


        Examples
        --------
        .. manim:: PointsAsCornersExample
            :save_last_frame:

            class PointsAsCornersExample(Scene):
                def construct(self):
                    corners = (
                        # create square
                        UR, UL,
                        DL, DR,
                        UR,
                        # create crosses
                        DL, UL,
                        DR
                    )
                    vmob = VMobject(stroke_color=RED)
                    vmob.set_points_as_corners(corners).scale(2)
                    self.add(vmob)
        """
        points = np.array(points)
        # This will set the handles aligned with the anchors.
        # Id est, a bezier curve will be the segment from the two anchors such that the handles belongs to this segment.
        self.set_anchors_and_handles(
            *(interpolate(points[:-1], points[1:], t) for t in self._bezier_t_values)
        )
        return self

    def set_points_smoothly(self, points: Point3DLike_Array) -> Self:
        self.set_points_as_corners(points)
        self.make_smooth()
        return self

    def change_anchor_mode(self, mode: Literal["jagged", "smooth"]) -> Self:
        """Changes the anchor mode of the bezier curves. This will modify the handles.

        There can be only two modes, "jagged", and "smooth".

        Returns
        -------
        :class:`VMobject`
            ``self``
        """
        assert mode in ["jagged", "smooth"], 'mode must be either "jagged" or "smooth"'
        nppcc = self.n_points_per_cubic_curve
        for submob in self.family_members_with_points():
            subpaths = submob.get_subpaths()
            submob.clear_points()
            # A subpath can be composed of several bezier curves.
            for subpath in subpaths:
                # This will retrieve the anchors of the subpath, by selecting every n element in the array subpath
                # The append is needed as the last element is not reached when slicing with numpy.
                anchors = np.append(subpath[::nppcc], subpath[-1:], 0)
                if mode == "smooth":
                    h1, h2 = get_smooth_cubic_bezier_handle_points(anchors)
                else:  # mode == "jagged"
                    # The following will make the handles aligned with the anchors, thus making the bezier curve a segment
                    a1 = anchors[:-1]
                    a2 = anchors[1:]
                    h1 = interpolate(a1, a2, 1.0 / 3)
                    h2 = interpolate(a1, a2, 2.0 / 3)
                new_subpath = np.array(subpath)
                new_subpath[1::nppcc] = h1
                new_subpath[2::nppcc] = h2
                submob.append_points(new_subpath)
        return self

    def make_smooth(self) -> Self:
        return self.change_anchor_mode("smooth")

    def make_jagged(self) -> Self:
        return self.change_anchor_mode("jagged")

    def add_subpath(self, points: CubicBezierPathLike) -> Self:
        assert len(points) % 4 == 0
        self.append_points(points)
        return self

    def append_vectorized_mobject(self, vectorized_mobject: VMobject) -> None:
        if self.has_new_path_started():
            # Remove last point, which is starting
            # a new path
            self.points = self.points[:-1]
        self.append_points(vectorized_mobject.points)

    def apply_function(self, function: MappingFunction) -> Self:
        factor = self.pre_function_handle_to_anchor_scale_factor
        self.scale_handle_to_anchor_distances(factor)
        super().apply_function(function)
        self.scale_handle_to_anchor_distances(1.0 / factor)
        if self.make_smooth_after_applying_functions:
            self.make_smooth()
        return self

    def rotate(
        self,
        angle: float,
        axis: Vector3DLike = OUT,
        about_point: Point3DLike | None = None,
        **kwargs,
    ) -> Self:
        self.rotate_sheen_direction(angle, axis)
        super().rotate(angle, axis, about_point, **kwargs)
        return self

    def scale_handle_to_anchor_distances(self, factor: float) -> Self:
        """If the distance between a given handle point H and its associated
        anchor point A is d, then it changes H to be a distances factor*d
        away from A, but so that the line from A to H doesn't change.
        This is mostly useful in the context of applying a (differentiable)
        function, to preserve tangency properties.  One would pull all the
        handles closer to their anchors, apply the function then push them out
        again.

        Parameters
        ----------
        factor
            The factor used for scaling.

        Returns
        -------
        :class:`VMobject`
            ``self``
        """
        for submob in self.family_members_with_points():
            if len(submob.points) < self.n_points_per_cubic_curve:
                # The case that a bezier quad is not complete (there is no bezier curve as there is not enough control points.)
                continue
            a1, h1, h2, a2 = submob.get_anchors_and_handles()
            a1_to_h1 = h1 - a1
            a2_to_h2 = h2 - a2
            new_h1 = a1 + factor * a1_to_h1
            new_h2 = a2 + factor * a2_to_h2
            submob.set_anchors_and_handles(a1, new_h1, new_h2, a2)
        return self

    #
    def consider_points_equals(self, p0: Point3DLike, p1: Point3DLike) -> bool:
        return np.allclose(p0, p1, atol=self.tolerance_for_point_equality)

    def consider_points_equals_2d(self, p0: Point2DLike, p1: Point2DLike) -> bool:
        """Determine if two points are close enough to be considered equal.

        This uses the algorithm from np.isclose(), but expanded here for the
        2D point case. NumPy is overkill for such a small question.
        Parameters
        ----------
        p0
            first point
        p1
            second point

        Returns
        -------
        bool
            whether two points considered close.
        """
        rtol = 1.0e-5  # default from np.isclose()
        atol = self.tolerance_for_point_equality
        if abs(p0[0] - p1[0]) > atol + rtol * abs(p1[0]):
            return False
        return abs(p0[1] - p1[1]) <= atol + rtol * abs(p1[1])

    # Information about line
    def get_cubic_bezier_tuples_from_points(
        self, points: CubicBezierPathLike
    ) -> CubicBezierPoints_Array:
        return np.array(self.gen_cubic_bezier_tuples_from_points(points))

    def gen_cubic_bezier_tuples_from_points(
        self, points: CubicBezierPathLike
    ) -> tuple[CubicBezierPointsLike, ...]:
        """Returns the bezier tuples from an array of points.

        self.points is a list of the anchors and handles of the bezier curves of the mobject (ie [anchor1, handle1, handle2, anchor2, anchor3 ..])
        This algorithm basically retrieve them by taking an element every n, where n is the number of control points
        of the bezier curve.


        Parameters
        ----------
        points
            Points from which control points will be extracted.

        Returns
        -------
        tuple
            Bezier control points.
        """
        nppcc = self.n_points_per_cubic_curve
        remainder = len(points) % nppcc
        points = points[: len(points) - remainder]
        # Basically take every nppcc element.
        return tuple(points[i : i + nppcc] for i in range(0, len(points), nppcc))

    def get_cubic_bezier_tuples(self) -> CubicBezierPoints_Array:
        return self.get_cubic_bezier_tuples_from_points(self.points)

    def _gen_subpaths_from_points(
        self,
        points: CubicBezierPath,
        filter_func: Callable[[int], bool],
    ) -> Iterable[CubicSpline]:
        """Given an array of points defining the bezier curves of the vmobject, return subpaths formed by these points.
        Here, Two bezier curves form a path if at least two of their anchors are evaluated True by the relation defined by filter_func.

        The algorithm every bezier tuple (anchors and handles) in ``self.points`` (by regrouping each n elements, where
        n is the number of points per cubic curve)), and evaluate the relation between two anchors with filter_func.
        NOTE : The filter_func takes an int n as parameter, and will evaluate the relation between points[n] and points[n - 1]. This should probably be changed so
        the function takes two points as parameters.

        Parameters
        ----------
        points
            points defining the bezier curve.
        filter_func
            Filter-func defining the relation.

        Returns
        -------
        Iterable[CubicSpline]
            subpaths formed by the points.
        """
        nppcc = self.n_points_per_cubic_curve
        filtered = filter(filter_func, range(nppcc, len(points), nppcc))
        split_indices = [0] + list(filtered) + [len(points)]
        return (
            points[i1:i2]
            for i1, i2 in zip(split_indices, split_indices[1:])
            if (i2 - i1) >= nppcc
        )

    def get_subpaths_from_points(self, points: CubicBezierPath) -> list[CubicSpline]:
        return list(
            self._gen_subpaths_from_points(
                points,
                lambda n: not self.consider_points_equals(points[n - 1], points[n]),
            ),
        )

    def gen_subpaths_from_points_2d(
        self, points: CubicBezierPath
    ) -> Iterable[CubicSpline]:
        return self._gen_subpaths_from_points(
            points,
            lambda n: not self.consider_points_equals_2d(points[n - 1], points[n]),
        )

    def get_subpaths(self) -> list[CubicSpline]:
        """Returns subpaths formed by the curves of the VMobject.

        Subpaths are ranges of curves with each pair of consecutive curves having their end/start points coincident.

        Returns
        -------
        list[CubicSpline]
            subpaths.
        """
        return self.get_subpaths_from_points(self.points)

    def get_nth_curve_points(self, n: int) -> CubicBezierPoints:
        """Returns the points defining the nth curve of the vmobject.

        Parameters
        ----------
        n
            index of the desired bezier curve.

        Returns
        -------
        CubicBezierPoints
            points defining the nth bezier curve (anchors, handles)
        """
        assert n < self.get_num_curves()
        nppcc = self.n_points_per_cubic_curve
        return self.points[nppcc * n : nppcc * (n + 1)]

    def get_nth_curve_function(self, n: int) -> Callable[[float], Point3D]:
        """Returns the expression of the nth curve.

        Parameters
        ----------
        n
            index of the desired curve.

        Returns
        -------
        Callable[float, Point3D]
            expression of the nth bezier curve.
        """
        return bezier(self.get_nth_curve_points(n))

    def get_nth_curve_length_pieces(
        self,
        n: int,
        sample_points: int | None = None,
    ) -> npt.NDArray[ManimFloat]:
        """Returns the array of short line lengths used for length approximation.

        Parameters
        ----------
        n
            The index of the desired curve.
        sample_points
            The number of points to sample to find the length.

        Returns
        -------
            The short length-pieces of the nth curve.
        """
        if sample_points is None:
            sample_points = 10

        curve = self.get_nth_curve_function(n)
        points = np.array([curve(a) for a in np.linspace(0, 1, sample_points)])
        diffs = points[1:] - points[:-1]
        norms = np.linalg.norm(diffs, axis=1)

        return norms

    def get_nth_curve_length(
        self,
        n: int,
        sample_points: int | None = None,
    ) -> float:
        """Returns the (approximate) length of the nth curve.

        Parameters
        ----------
        n
            The index of the desired curve.
        sample_points
            The number of points to sample to find the length.

        Returns
        -------
        length : :class:`float`
            The length of the nth curve.
        """
        _, length = self.get_nth_curve_function_with_length(n, sample_points)

        return length

    def get_nth_curve_function_with_length(
        self,
        n: int,
        sample_points: int | None = None,
    ) -> tuple[Callable[[float], Point3D], float]:
        """Returns the expression of the nth curve along with its (approximate) length.

        Parameters
        ----------
        n
            The index of the desired curve.
        sample_points
            The number of points to sample to find the length.

        Returns
        -------
        curve : Callable[[float], Point3D]
            The function for the nth curve.
        length : :class:`float`
            The length of the nth curve.
        """
        curve = self.get_nth_curve_function(n)
        norms = self.get_nth_curve_length_pieces(n, sample_points=sample_points)
        length = np.sum(norms)

        return curve, length

    def get_num_curves(self) -> int:
        """Returns the number of curves of the vmobject.

        Returns
        -------
        int
            number of curves of the vmobject.
        """
        nppcc = self.n_points_per_cubic_curve
        return len(self.points) // nppcc

    def get_curve_functions(
        self,
    ) -> Iterable[Callable[[float], Point3D]]:
        """Gets the functions for the curves of the mobject.

        Returns
        -------
        Iterable[Callable[[float], Point3D]]
            The functions for the curves.
        """
        num_curves = self.get_num_curves()

        for n in range(num_curves):
            yield self.get_nth_curve_function(n)

    def get_curve_functions_with_lengths(
        self, **kwargs
    ) -> Iterable[tuple[Callable[[float], Point3D], float]]:
        """Gets the functions and lengths of the curves for the mobject.

        Parameters
        ----------
        **kwargs
            The keyword arguments passed to :meth:`get_nth_curve_function_with_length`

        Returns
        -------
        Iterable[tuple[Callable[[float], Point3D], float]]
            The functions and lengths of the curves.
        """
        num_curves = self.get_num_curves()

        for n in range(num_curves):
            yield self.get_nth_curve_function_with_length(n, **kwargs)

    def point_from_proportion(self, alpha: float) -> Point3D:
        """Gets the point at a proportion along the path of the :class:`VMobject`.

        Parameters
        ----------
        alpha
            The proportion along the the path of the :class:`VMobject`.

        Returns
        -------
        :class:`numpy.ndarray`
            The point on the :class:`VMobject`.

        Raises
        ------
        :exc:`ValueError`
            If ``alpha`` is not between 0 and 1.
        :exc:`Exception`
            If the :class:`VMobject` has no points.

        Example
        -------
        .. manim:: PointFromProportion
            :save_last_frame:

            class PointFromProportion(Scene):
                def construct(self):
                    line = Line(2*DL, 2*UR)
                    self.add(line)
                    colors = (RED, BLUE, YELLOW)
                    proportions = (1/4, 1/2, 3/4)
                    for color, proportion in zip(colors, proportions):
                        self.add(Dot(color=color).move_to(
                                line.point_from_proportion(proportion)
                        ))
        """
        if alpha < 0 or alpha > 1:
            raise ValueError(f"Alpha {alpha} not between 0 and 1.")

        self.throw_error_if_no_points()
        if alpha == 1:
            return self.points[-1]

        curves_and_lengths = tuple(self.get_curve_functions_with_lengths())

        target_length = alpha * sum(length for _, length in curves_and_lengths)
        current_length = 0

        for curve, length in curves_and_lengths:
            if current_length + length >= target_length:
                if length != 0:
                    residue = (target_length - current_length) / length
                else:
                    residue = 0

                return curve(residue)

            current_length += length
        raise Exception(
            "Not sure how you reached here, please file a bug report at https://github.com/ManimCommunity/manim/issues/new/choose"
        )

    def proportion_from_point(
        self,
        point: Point3DLike,
    ) -> float:
        """Returns the proportion along the path of the :class:`VMobject`
        a particular given point is at.

        Parameters
        ----------
        point
            The Cartesian coordinates of the point which may or may not lie on the :class:`VMobject`

        Returns
        -------
        float
            The proportion along the path of the :class:`VMobject`.

        Raises
        ------
        :exc:`ValueError`
            If ``point`` does not lie on the curve.
        :exc:`Exception`
            If the :class:`VMobject` has no points.
        """
        self.throw_error_if_no_points()

        # Iterate over each bezier curve that the ``VMobject`` is composed of, checking
        # if the point lies on that curve. If it does not lie on that curve, add
        # the whole length of the curve to ``target_length`` and move onto the next
        # curve. If the point does lie on the curve, add how far along the curve
        # the point is to ``target_length``.
        # Then, divide ``target_length`` by the total arc length of the shape to get
        # the proportion along the ``VMobject`` the point is at.

        num_curves = self.get_num_curves()
        total_length = self.get_arc_length()
        target_length = 0
        for n in range(num_curves):
            control_points = self.get_nth_curve_points(n)
            length = self.get_nth_curve_length(n)
            proportions_along_bezier = proportions_along_bezier_curve_for_point(
                point,
                control_points,
            )
            if len(proportions_along_bezier) > 0:
                proportion_along_nth_curve = max(proportions_along_bezier)
                target_length += length * proportion_along_nth_curve
                break
            target_length += length
        else:
            raise ValueError(f"Point {point} does not lie on this curve.")

        alpha = target_length / total_length

        return alpha

    def get_anchors_and_handles(self) -> list[Point3D_Array]:
        """Returns anchors1, handles1, handles2, anchors2,
        where (anchors1[i], handles1[i], handles2[i], anchors2[i])
        will be four points defining a cubic bezier curve
        for any i in range(0, len(anchors1))

        Returns
        -------
        `list[Point3D_Array]`
            Iterable of the anchors and handles.
        """
        nppcc = self.n_points_per_cubic_curve
        return [self.points[i::nppcc] for i in range(nppcc)]

    def get_start_anchors(self) -> Point3D_Array:
        """Returns the start anchors of the bezier curves.

        Returns
        -------
        Point3D_Array
            Starting anchors
        """
        return self.points[:: self.n_points_per_cubic_curve]

    def get_end_anchors(self) -> Point3D_Array:
        """Return the end anchors of the bezier curves.

        Returns
        -------
        Point3D_Array
            Starting anchors
        """
        nppcc = self.n_points_per_cubic_curve
        return self.points[nppcc - 1 :: nppcc]

    def get_anchors(self) -> list[Point3D]:
        """Returns the anchors of the curves forming the VMobject.

        Returns
        -------
        Point3D_Array
            The anchors.
        """
        if self.points.shape[0] == 1:
            return self.points

        s = self.get_start_anchors()
        e = self.get_end_anchors()
        return list(it.chain.from_iterable(zip(s, e)))

    def get_points_defining_boundary(self) -> Point3D_Array:
        # Probably returns all anchors, but this is weird regarding  the name of the method.
        return np.array(
            tuple(it.chain(*(sm.get_anchors() for sm in self.get_family())))
        )

    def get_arc_length(self, sample_points_per_curve: int | None = None) -> float:
        """Return the approximated length of the whole curve.

        Parameters
        ----------
        sample_points_per_curve
            Number of sample points per curve used to approximate the length. More points result in a better approximation.

        Returns
        -------
        float
            The length of the :class:`VMobject`.
        """
        return sum(
            length
            for _, length in self.get_curve_functions_with_lengths(
                sample_points=sample_points_per_curve,
            )
        )

    # Alignment
    def align_points(self, vmobject: VMobject) -> Self:
        """Adds points to self and vmobject so that they both have the same number of subpaths, with
        corresponding subpaths each containing the same number of points.

        Points are added either by subdividing curves evenly along the subpath, or by creating new subpaths consisting
        of a single point repeated.

        Parameters
        ----------
        vmobject
            The object to align points with.

        Returns
        -------
        :class:`VMobject`
           ``self``

        See also
        --------
        :meth:`~.Mobject.interpolate`, :meth:`~.Mobject.align_data`
        """
        self.align_rgbas(vmobject)
        # TODO: This shortcut can be a bit over eager. What if they have the same length, but different subpath lengths?
        if self.get_num_points() == vmobject.get_num_points():
            return

        for mob in self, vmobject:
            # If there are no points, add one to
            # wherever the "center" is
            if mob.has_no_points():
                mob.start_new_path(mob.get_center())
            # If there's only one point, turn it into
            # a null curve
            if mob.has_new_path_started():
                mob.add_line_to(mob.get_last_point())

        # Figure out what the subpaths are
        subpaths1 = self.get_subpaths()
        subpaths2 = vmobject.get_subpaths()
        n_subpaths = max(len(subpaths1), len(subpaths2))
        # Start building new ones
        new_path1 = np.zeros((0, self.dim))
        new_path2 = np.zeros((0, self.dim))

        nppcc = self.n_points_per_cubic_curve

        def get_nth_subpath(path_list, n):
            if n >= len(path_list):
                # Create a null path at the very end
                return [path_list[-1][-1]] * nppcc
            path = path_list[n]
            # Check for useless points at the end of the path and remove them
            # https://github.com/ManimCommunity/manim/issues/1959
            while len(path) > nppcc:
                # If the last nppc points are all equal to the preceding point
                if self.consider_points_equals(path[-nppcc:], path[-nppcc - 1]):
                    path = path[:-nppcc]
                else:
                    break
            return path

        for n in range(n_subpaths):
            # For each pair of subpaths, add points until they are the same length
            sp1 = get_nth_subpath(subpaths1, n)
            sp2 = get_nth_subpath(subpaths2, n)
            diff1 = max(0, (len(sp2) - len(sp1)) // nppcc)
            diff2 = max(0, (len(sp1) - len(sp2)) // nppcc)
            sp1 = self.insert_n_curves_to_point_list(diff1, sp1)
            sp2 = self.insert_n_curves_to_point_list(diff2, sp2)
            new_path1 = np.append(new_path1, sp1, axis=0)
            new_path2 = np.append(new_path2, sp2, axis=0)
        self.set_points(new_path1)
        vmobject.set_points(new_path2)
        return self

    def insert_n_curves(self, n: int) -> Self:
        """Inserts n curves to the bezier curves of the vmobject.

        Parameters
        ----------
        n
            Number of curves to insert.

        Returns
        -------
        :class:`VMobject`
            ``self``
        """
        new_path_point = None
        if self.has_new_path_started():
            new_path_point = self.get_last_point()

        new_points = self.insert_n_curves_to_point_list(n, self.points)
        self.set_points(new_points)

        if new_path_point is not None:
            self.append_points([new_path_point])
        return self

    def insert_n_curves_to_point_list(
        self, n: int, points: BezierPathLike
    ) -> BezierPath:
        """Given an array of k points defining a bezier curves (anchors and handles), returns points defining exactly k + n bezier curves.

        Parameters
        ----------
        n
            Number of desired curves.
        points
            Starting points.

        Returns
        -------
            Points generated.
        """
        if len(points) == 1:
            nppcc = self.n_points_per_cubic_curve
            return np.repeat(points, nppcc * n, 0)
        bezier_tuples = self.get_cubic_bezier_tuples_from_points(points)
        current_number_of_curves = len(bezier_tuples)
        new_number_of_curves = current_number_of_curves + n
        new_bezier_tuples = bezier_remap(bezier_tuples, new_number_of_curves)
        new_points = new_bezier_tuples.reshape(-1, 3)
        return new_points

    def align_rgbas(self, vmobject: VMobject) -> Self:
        attrs = ["fill_rgbas", "stroke_rgbas", "background_stroke_rgbas"]
        for attr in attrs:
            a1 = getattr(self, attr)
            a2 = getattr(vmobject, attr)
            if len(a1) > len(a2):
                new_a2 = stretch_array_to_length(a2, len(a1))
                setattr(vmobject, attr, new_a2)
            elif len(a2) > len(a1):
                new_a1 = stretch_array_to_length(a1, len(a2))
                setattr(self, attr, new_a1)
        return self

    def get_point_mobject(self, center: Point3DLike | None = None) -> VectorizedPoint:
        if center is None:
            center = self.get_center()
        point = VectorizedPoint(center)
        point.match_style(self)
        return point

    def interpolate_color(
        self, mobject1: VMobject, mobject2: VMobject, alpha: float
    ) -> None:
        attrs = [
            "fill_rgbas",
            "stroke_rgbas",
            "background_stroke_rgbas",
            "stroke_width",
            "background_stroke_width",
            "sheen_direction",
            "sheen_factor",
        ]
        for attr in attrs:
            setattr(
                self,
                attr,
                interpolate(getattr(mobject1, attr), getattr(mobject2, attr), alpha),
            )
            if alpha == 1.0:
                val = getattr(mobject2, attr)
                if isinstance(val, np.ndarray):
                    val = val.copy()
                setattr(self, attr, val)

    def pointwise_become_partial(
        self,
        vmobject: VMobject,
        a: float,
        b: float,
    ) -> Self:
        """Given a 2nd :class:`.VMobject` ``vmobject``, a lower bound ``a`` and
        an upper bound ``b``, modify this :class:`.VMobject`'s points to
        match the portion of the Bézier spline described by ``vmobject.points``
        with the parameter ``t`` between ``a`` and ``b``.

        Parameters
        ----------
        vmobject
            The :class:`.VMobject` that will serve as a model.
        a
            The lower bound for ``t``.
        b
            The upper bound for ``t``

        Returns
        -------
        :class:`.VMobject`
            The :class:`.VMobject` itself, after the transformation.

        Raises
        ------
        TypeError
            If ``vmobject`` is not an instance of :class:`VMobject`.
        """
        if not isinstance(vmobject, VMobject):
            raise TypeError(
                f"Expected a VMobject, got value {vmobject} of type "
                f"{type(vmobject).__name__}."
            )
        # Partial curve includes three portions:
        # - A middle section, which matches the curve exactly.
        # - A start, which is some ending portion of an inner cubic.
        # - An end, which is the starting portion of a later inner cubic.
        if a <= 0 and b >= 1:
            self.set_points(vmobject.points)
            return self
        num_curves = vmobject.get_num_curves()
        if num_curves == 0:
            return self

        # The following two lines will compute which Bézier curves of the given Mobject must be processed.
        # The residue indicates the proportion of the selected Bézier curve which must be selected.
        #
        # Example: if num_curves is 10, a is 0.34 and b is 0.78, then:
        # - lower_index is 3 and lower_residue is 0.4, which means the algorithm will look at the 3rd Bézier
        #   and select its part which ranges from t=0.4 to t=1.
        # - upper_index is 7 and upper_residue is 0.8, which means the algorithm will look at the 7th Bézier
        #   and select its part which ranges from t=0 to t=0.8.
        lower_index, lower_residue = integer_interpolate(0, num_curves, a)
        upper_index, upper_residue = integer_interpolate(0, num_curves, b)

        nppc = self.n_points_per_curve

        # Copy vmobject.points if vmobject is self to prevent unintended in-place modification
        vmobject_points = (
            vmobject.points.copy() if self is vmobject else vmobject.points
        )

        # If both indices coincide, get a part of a single Bézier curve.
        if lower_index == upper_index:
            # Look at the "lower_index"-th Bézier curve and select its part from
            # t=lower_residue to t=upper_residue.
            self.points = partial_bezier_points(
                vmobject_points[nppc * lower_index : nppc * (lower_index + 1)],
                lower_residue,
                upper_residue,
            )
        else:
            # Allocate space for (upper_index-lower_index+1) Bézier curves.
            self.points = np.empty((nppc * (upper_index - lower_index + 1), self.dim))
            # Look at the "lower_index"-th Bezier curve and select its part from
            # t=lower_residue to t=1. This is the first curve in self.points.
            self.points[:nppc] = partial_bezier_points(
                vmobject_points[nppc * lower_index : nppc * (lower_index + 1)],
                lower_residue,
                1,
            )
            # If there are more curves between the "lower_index"-th and the
            # "upper_index"-th Béziers, add them all to self.points.
            self.points[nppc:-nppc] = vmobject_points[
                nppc * (lower_index + 1) : nppc * upper_index
            ]
            # Look at the "upper_index"-th Bézier curve and select its part from
            # t=0 to t=upper_residue. This is the last curve in self.points.
            self.points[-nppc:] = partial_bezier_points(
                vmobject_points[nppc * upper_index : nppc * (upper_index + 1)],
                0,
                upper_residue,
            )

        return self

    def get_subcurve(self, a: float, b: float) -> Self:
        """Returns the subcurve of the VMobject between the interval [a, b].
        The curve is a VMobject itself.

        Parameters
        ----------

        a
            The lower bound.
        b
            The upper bound.

        Returns
        -------
        VMobject
            The subcurve between of [a, b]
        """
        if self.is_closed() and a > b:
            vmob = self.copy()
            vmob.pointwise_become_partial(self, a, 1)
            vmob2 = self.copy()
            vmob2.pointwise_become_partial(self, 0, b)
            vmob.append_vectorized_mobject(vmob2)
        else:
            vmob = self.copy()
            vmob.pointwise_become_partial(self, a, b)
        return vmob

    def get_direction(self) -> Literal["CW", "CCW"]:
        """Uses :func:`~.space_ops.shoelace_direction` to calculate the direction.
        The direction of points determines in which direction the
        object is drawn, clockwise or counterclockwise.

        Examples
        --------
        The default direction of a :class:`~.Circle` is counterclockwise::

            >>> from manim import Circle
            >>> Circle().get_direction()
            'CCW'

        Returns
        -------
        :class:`str`
            Either ``"CW"`` or ``"CCW"``.
        """
        return shoelace_direction(self.get_start_anchors())

    def reverse_direction(self) -> Self:
        """Reverts the point direction by inverting the point order.

        Returns
        -------
        :class:`VMobject`
            Returns self.

        Examples
        --------
        .. manim:: ChangeOfDirection

            class ChangeOfDirection(Scene):
                def construct(self):
                    ccw = RegularPolygon(5)
                    ccw.shift(LEFT)
                    cw = RegularPolygon(5)
                    cw.shift(RIGHT).reverse_direction()

                    self.play(Create(ccw), Create(cw),
                    run_time=4)
        """
        self.points = self.points[::-1]
        return self

    def force_direction(self, target_direction: Literal["CW", "CCW"]) -> Self:
        """Makes sure that points are either directed clockwise or
        counterclockwise.

        Parameters
        ----------
        target_direction
            Either ``"CW"`` or ``"CCW"``.
        """
        if target_direction not in ("CW", "CCW"):
            raise ValueError('Invalid input for force_direction. Use "CW" or "CCW"')
        if self.get_direction() != target_direction:
            # Since we already assured the input is CW or CCW,
            # and the directions don't match, we just reverse
            self.reverse_direction()
        return self


class VGroup(VMobject, metaclass=ConvertToOpenGL):
    """A group of vectorized mobjects.

    This can be used to group multiple :class:`~.VMobject` instances together
    in order to scale, move, ... them together.

    Notes
    -----
    When adding the same mobject more than once, repetitions are ignored.
    Use :meth:`.Mobject.copy` to create a separate copy which can then
    be added to the group.

    Examples
    --------

    To add :class:`~.VMobject`s to a :class:`~.VGroup`, you can either use the
    :meth:`~.VGroup.add` method, or use the `+` and `+=` operators. Similarly, you
    can subtract elements of a VGroup via :meth:`~.VGroup.remove` method, or
    `-` and `-=` operators:

        >>> from manim import Triangle, Square, VGroup
        >>> vg = VGroup()
        >>> triangle, square = Triangle(), Square()
        >>> vg.add(triangle)
        VGroup(Triangle)
        >>> vg + square  # a new VGroup is constructed
        VGroup(Triangle, Square)
        >>> vg  # not modified
        VGroup(Triangle)
        >>> vg += square
        >>> vg  # modifies vg
        VGroup(Triangle, Square)
        >>> vg.remove(triangle)
        VGroup(Square)
        >>> vg - square  # a new VGroup is constructed
        VGroup()
        >>> vg  # not modified
        VGroup(Square)
        >>> vg -= square
        >>> vg  # modifies vg
        VGroup()

    .. manim:: ArcShapeIris
        :save_last_frame:

        class ArcShapeIris(Scene):
            def construct(self):
                colors = [DARK_BROWN, BLUE_E, BLUE_D, BLUE_A, TEAL_B, GREEN_B, YELLOW_E]
                radius = [1 + rad * 0.1 for rad in range(len(colors))]

                circles_group = VGroup()

                # zip(radius, color) makes the iterator [(radius[i], color[i]) for i in range(radius)]
                circles_group.add(*[Circle(radius=rad, stroke_width=10, color=col)
                                    for rad, col in zip(radius, colors)])
                self.add(circles_group)

    """

    def __init__(
        self, *vmobjects: VMobject | Iterable[VMobject], **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.add(*vmobjects)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({', '.join(str(mob) for mob in self.submobjects)})"

    def __str__(self) -> str:
        return (
            f"{self.__class__.__name__} of {len(self.submobjects)} "
            f"submobject{'s' if len(self.submobjects) > 0 else ''}"
        )

    def add(
        self,
        *vmobjects: VMobject | Iterable[VMobject],
    ) -> Self:
        """Checks if all passed elements are an instance, or iterables of VMobject and then adds them to submobjects

        Parameters
        ----------
        vmobjects
            List or iterable of VMobjects to add

        Returns
        -------
        :class:`VGroup`

        Raises
        ------
        TypeError
            If one element of the list, or iterable is not an instance of VMobject

        Examples
        --------
        The following example shows how to add individual or multiple `VMobject` instances through the `VGroup`
        constructor and its `.add()` method.

        .. manim:: AddToVGroup

            class AddToVGroup(Scene):
                def construct(self):
                    circle_red = Circle(color=RED)
                    circle_green = Circle(color=GREEN)
                    circle_blue = Circle(color=BLUE)
                    circle_red.shift(LEFT)
                    circle_blue.shift(RIGHT)
                    gr = VGroup(circle_red, circle_green)
                    gr2 = VGroup(circle_blue) # Constructor uses add directly
                    self.add(gr,gr2)
                    self.wait()
                    gr += gr2 # Add group to another
                    self.play(
                        gr.animate.shift(DOWN),
                    )
                    gr -= gr2 # Remove group
                    self.play( # Animate groups separately
                        gr.animate.shift(LEFT),
                        gr2.animate.shift(UP),
                    )
                    self.play( #Animate groups without modification
                        (gr+gr2).animate.shift(RIGHT)
                    )
                    self.play( # Animate group without component
                        (gr-circle_red).animate.shift(RIGHT)
                    )

        A `VGroup` can be created using iterables as well. Keep in mind that all generated values from an
        iterable must be an instance of `VMobject`. This is demonstrated below:

        .. manim:: AddIterableToVGroupExample
            :save_last_frame:

            class AddIterableToVGroupExample(Scene):
                def construct(self):
                    v = VGroup(
                        Square(),               # Singular VMobject instance
                        [Circle(), Triangle()], # List of VMobject instances
                        Dot(),
                        (Dot() for _ in range(2)), # Iterable that generates VMobjects
                    )
                    v.arrange()
                    self.add(v)

        To facilitate this, the iterable is unpacked before its individual instances are added to the `VGroup`.
        As a result, when you index a `VGroup`, you will never get back an iterable.
        Instead, you will always receive `VMobject` instances, including those
        that were part of the iterable/s that you originally added to the `VGroup`.
        """

        def get_type_error_message(invalid_obj, invalid_indices):
            return (
                f"Only values of type {vmobject_render_type.__name__} can be added "
                "as submobjects of VGroup, but the value "
                f"{repr(invalid_obj)} (at index {invalid_indices[1]} of "
                f"parameter {invalid_indices[0]}) is of type "
                f"{type(invalid_obj).__name__}."
            )

        vmobject_render_type = (
            OpenGLVMobject if config.renderer == RendererType.OPENGL else VMobject
        )
        valid_vmobjects = []

        for i, vmobject in enumerate(vmobjects):
            if isinstance(vmobject, vmobject_render_type):
                valid_vmobjects.append(vmobject)
            elif isinstance(vmobject, Iterable) and not isinstance(
                vmobject, (Mobject, OpenGLMobject)
            ):
                for j, subvmobject in enumerate(vmobject):
                    if not isinstance(subvmobject, vmobject_render_type):
                        raise TypeError(get_type_error_message(subvmobject, (i, j)))
                    valid_vmobjects.append(subvmobject)
            elif isinstance(vmobject, Iterable) and isinstance(
                vmobject, (Mobject, OpenGLMobject)
            ):
                raise TypeError(
                    f"{get_type_error_message(vmobject, (i, 0))} "
                    "You can try adding this value into a Group instead."
                )
            else:
                raise TypeError(get_type_error_message(vmobject, (i, 0)))

        return super().add(*valid_vmobjects)

    def __add__(self, vmobject: VMobject) -> Self:
        return VGroup(*self.submobjects, vmobject)

    def __iadd__(self, vmobject: VMobject) -> Self:
        return self.add(vmobject)

    def __sub__(self, vmobject: VMobject) -> Self:
        copy = VGroup(*self.submobjects)
        copy.remove(vmobject)
        return copy

    def __isub__(self, vmobject: VMobject) -> Self:
        return self.remove(vmobject)

    def __setitem__(self, key: int, value: VMobject | Sequence[VMobject]) -> None:
        """Override the [] operator for item assignment.

        Parameters
        ----------
        key
            The index of the submobject to be assigned
        value
            The vmobject value to assign to the key

        Returns
        -------
        None

        Tests
        -----
        Check that item assignment does not raise error::
            >>> vgroup = VGroup(VMobject())
            >>> new_obj = VMobject()
            >>> vgroup[0] = new_obj
        """
        self._assert_valid_submobjects(tuplify(value))
        self.submobjects[key] = value


class VDict(VMobject, metaclass=ConvertToOpenGL):
    """A VGroup-like class, also offering submobject access by
    key, like a python dict

    Parameters
    ----------
    mapping_or_iterable
            The parameter specifying the key-value mapping of keys and mobjects.
    show_keys
            Whether to also display the key associated with
            the mobject. This might be useful when debugging,
            especially when there are a lot of mobjects in the
            :class:`VDict`. Defaults to False.
    kwargs
            Other arguments to be passed to `Mobject`.

    Attributes
    ----------
    show_keys : :class:`bool`
            Whether to also display the key associated with
            the mobject. This might be useful when debugging,
            especially when there are a lot of mobjects in the
            :class:`VDict`. When displayed, the key is towards
            the left of the mobject.
            Defaults to False.
    submob_dict : :class:`dict`
            Is the actual python dictionary that is used to bind
            the keys to the mobjects.

    Examples
    --------

    .. manim:: ShapesWithVDict

        class ShapesWithVDict(Scene):
            def construct(self):
                square = Square().set_color(RED)
                circle = Circle().set_color(YELLOW).next_to(square, UP)

                # create dict from list of tuples each having key-mobject pair
                pairs = [("s", square), ("c", circle)]
                my_dict = VDict(pairs, show_keys=True)

                # display it just like a VGroup
                self.play(Create(my_dict))
                self.wait()

                text = Tex("Some text").set_color(GREEN).next_to(square, DOWN)

                # add a key-value pair by wrapping it in a single-element list of tuple
                # after attrs branch is merged, it will be easier like `.add(t=text)`
                my_dict.add([("t", text)])
                self.wait()

                rect = Rectangle().next_to(text, DOWN)
                # can also do key assignment like a python dict
                my_dict["r"] = rect

                # access submobjects like a python dict
                my_dict["t"].set_color(PURPLE)
                self.play(my_dict["t"].animate.scale(3))
                self.wait()

                # also supports python dict styled reassignment
                my_dict["t"] = Tex("Some other text").set_color(BLUE)
                self.wait()

                # remove submobject by key
                my_dict.remove("t")
                self.wait()

                self.play(Uncreate(my_dict["s"]))
                self.wait()

                self.play(FadeOut(my_dict["c"]))
                self.wait()

                self.play(FadeOut(my_dict["r"], shift=DOWN))
                self.wait()

                # you can also make a VDict from an existing dict of mobjects
                plain_dict = {
                    1: Integer(1).shift(DOWN),
                    2: Integer(2).shift(2 * DOWN),
                    3: Integer(3).shift(3 * DOWN),
                }

                vdict_from_plain_dict = VDict(plain_dict)
                vdict_from_plain_dict.shift(1.5 * (UP + LEFT))
                self.play(Create(vdict_from_plain_dict))

                # you can even use zip
                vdict_using_zip = VDict(zip(["s", "c", "r"], [Square(), Circle(), Rectangle()]))
                vdict_using_zip.shift(1.5 * RIGHT)
                self.play(Create(vdict_using_zip))
                self.wait()
    """

    def __init__(
        self,
        mapping_or_iterable: (
            Mapping[Hashable, VMobject] | Iterable[tuple[Hashable, VMobject]]
        ) = {},
        show_keys: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.show_keys = show_keys
        self.submob_dict = {}
        self.add(mapping_or_iterable)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({repr(self.submob_dict)})"

    def add(
        self,
        mapping_or_iterable: (
            Mapping[Hashable, VMobject] | Iterable[tuple[Hashable, VMobject]]
        ),
    ) -> Self:
        """Adds the key-value pairs to the :class:`VDict` object.

        Also, it internally adds the value to the `submobjects` :class:`list`
        of :class:`~.Mobject`, which is responsible for actual on-screen display.

        Parameters
        ---------
        mapping_or_iterable
            The parameter specifying the key-value mapping of keys and mobjects.

        Returns
        -------
        :class:`VDict`
            Returns the :class:`VDict` object on which this method was called.

        Examples
        --------
        Normal usage::

            square_obj = Square()
            my_dict.add([("s", square_obj)])
        """
        for key, value in dict(mapping_or_iterable).items():
            self.add_key_value_pair(key, value)

        return self

    def remove(self, key: Hashable) -> Self:
        """Removes the mobject from the :class:`VDict` object having the key `key`

        Also, it internally removes the mobject from the `submobjects` :class:`list`
        of :class:`~.Mobject`, (which is responsible for removing it from the screen)

        Parameters
        ----------
        key
            The key of the submoject to be removed.

        Returns
        -------
        :class:`VDict`
            Returns the :class:`VDict` object on which this method was called.

        Examples
        --------
        Normal usage::

            my_dict.remove("square")
        """
        if key not in self.submob_dict:
            raise KeyError(f"The given key '{key!s}' is not present in the VDict")
        super().remove(self.submob_dict[key])
        del self.submob_dict[key]
        return self

    def __getitem__(self, key: Hashable):
        """Override the [] operator for item retrieval.

        Parameters
        ----------
        key
           The key of the submoject to be accessed

        Returns
        -------
        :class:`VMobject`
           The submobject corresponding to the key `key`

        Examples
        --------
        Normal usage::

           self.play(Create(my_dict["s"]))
        """
        submob = self.submob_dict[key]
        return submob

    def __setitem__(self, key: Hashable, value: VMobject) -> None:
        """Override the [] operator for item assignment.

        Parameters
        ----------
        key
            The key of the submoject to be assigned
        value
            The submobject to bind the key to

        Returns
        -------
        None

        Examples
        --------
        Normal usage::

            square_obj = Square()
            my_dict["sq"] = square_obj
        """
        if key in self.submob_dict:
            self.remove(key)
        self.add([(key, value)])

    def __delitem__(self, key: Hashable):
        """Override the del operator for deleting an item.

        Parameters
        ----------
        key
            The key of the submoject to be deleted

        Returns
        -------
        None

        Examples
        --------
        ::

            >>> from manim import *
            >>> my_dict = VDict({'sq': Square()})
            >>> 'sq' in my_dict
            True
            >>> del my_dict['sq']
            >>> 'sq' in my_dict
            False

        Notes
        -----
        Removing an item from a VDict does not remove that item from any Scene
        that the VDict is part of.

        """
        del self.submob_dict[key]

    def __contains__(self, key: Hashable):
        """Override the in operator.

        Parameters
        ----------
        key
            The key to check membership of.

        Returns
        -------
        :class:`bool`

        Examples
        --------
        ::

            >>> from manim import *
            >>> my_dict = VDict({'sq': Square()})
            >>> 'sq' in my_dict
            True

        """
        return key in self.submob_dict

    def get_all_submobjects(self) -> list[list]:
        """To get all the submobjects associated with a particular :class:`VDict` object

        Returns
        -------
        :class:`dict_values`
            All the submobjects associated with the :class:`VDict` object

        Examples
        --------
        Normal usage::

            for submob in my_dict.get_all_submobjects():
                self.play(Create(submob))
        """
        submobjects = self.submob_dict.values()
        return submobjects

    def add_key_value_pair(self, key: Hashable, value: VMobject) -> None:
        """A utility function used by :meth:`add` to add the key-value pair
        to :attr:`submob_dict`. Not really meant to be used externally.

        Parameters
        ----------
        key
            The key of the submobject to be added.
        value
            The mobject associated with the key

        Returns
        -------
        None

        Raises
        ------
        TypeError
            If the value is not an instance of VMobject

        Examples
        --------
        Normal usage::

            square_obj = Square()
            self.add_key_value_pair("s", square_obj)

        """
        self._assert_valid_submobjects([value])
        mob = value
        if self.show_keys:
            # This import is here and not at the top to avoid circular import
            from manim.mobject.text.tex_mobject import Tex

            key_text = Tex(str(key)).next_to(value, LEFT)
            mob.add(key_text)

        self.submob_dict[key] = mob
        super().add(value)


class VectorizedPoint(VMobject, metaclass=ConvertToOpenGL):
    def __init__(
        self,
        location: Point3DLike = ORIGIN,
        color: ManimColor = BLACK,
        fill_opacity: float = 0,
        stroke_width: float = 0,
        artificial_width: float = 0.01,
        artificial_height: float = 0.01,
        **kwargs,
    ) -> None:
        self.artificial_width = artificial_width
        self.artificial_height = artificial_height
        super().__init__(
            color=color,
            fill_opacity=fill_opacity,
            stroke_width=stroke_width,
            **kwargs,
        )
        self.set_points(np.array([location]))

    basecls = OpenGLVMobject if config.renderer == RendererType.OPENGL else VMobject

    @basecls.width.getter
    def width(self) -> float:
        return self.artificial_width

    @basecls.height.getter
    def height(self) -> float:
        return self.artificial_height

    def get_location(self) -> Point3D:
        return np.array(self.points[0])

    def set_location(self, new_loc: Point3D):
        self.set_points(np.array([new_loc]))


class CurvesAsSubmobjects(VGroup):
    """Convert a curve's elements to submobjects.

    Examples
    --------
    .. manim:: LineGradientExample
        :save_last_frame:

        class LineGradientExample(Scene):
            def construct(self):
                curve = ParametricFunction(lambda t: [t, np.sin(t), 0], t_range=[-PI, PI, 0.01], stroke_width=10)
                new_curve = CurvesAsSubmobjects(curve)
                new_curve.set_color_by_gradient(BLUE, RED)
                self.add(new_curve.shift(UP), curve)

    """

    def __init__(self, vmobject: VMobject, **kwargs) -> None:
        super().__init__(**kwargs)
        tuples = vmobject.get_cubic_bezier_tuples()
        for tup in tuples:
            part = VMobject()
            part.set_points(tup)
            part.match_style(vmobject)
            self.add(part)

    def point_from_proportion(self, alpha: float) -> Point3D:
        """Gets the point at a proportion along the path of the :class:`CurvesAsSubmobjects`.

        Parameters
        ----------
        alpha
            The proportion along the the path of the :class:`CurvesAsSubmobjects`.

        Returns
        -------
        :class:`numpy.ndarray`
            The point on the :class:`CurvesAsSubmobjects`.

        Raises
        ------
        :exc:`ValueError`
            If ``alpha`` is not between 0 and 1.
        :exc:`Exception`
            If the :class:`CurvesAsSubmobjects` has no submobjects, or no submobject has points.
        """
        if alpha < 0 or alpha > 1:
            raise ValueError(f"Alpha {alpha} not between 0 and 1.")

        self._throw_error_if_no_submobjects()
        submobjs_with_pts = self._get_submobjects_with_points()

        if alpha == 1:
            return submobjs_with_pts[-1].points[-1]

        submobjs_arc_lengths = tuple(
            part.get_arc_length() for part in submobjs_with_pts
        )

        total_length = sum(submobjs_arc_lengths)
        target_length = alpha * total_length
        current_length = 0

        for i, part in enumerate(submobjs_with_pts):
            part_length = submobjs_arc_lengths[i]
            if current_length + part_length >= target_length:
                residue = (target_length - current_length) / part_length
                return part.point_from_proportion(residue)

            current_length += part_length

    def _throw_error_if_no_submobjects(self):
        if len(self.submobjects) == 0:
            caller_name = sys._getframe(1).f_code.co_name
            raise Exception(
                f"Cannot call CurvesAsSubmobjects. {caller_name} for a CurvesAsSubmobject with no submobjects"
            )

    def _get_submobjects_with_points(self):
        submobjs_with_pts = tuple(
            part for part in self.submobjects if len(part.points) > 0
        )
        if len(submobjs_with_pts) == 0:
            caller_name = sys._getframe(1).f_code.co_name
            raise Exception(
                f"Cannot call CurvesAsSubmobjects. {caller_name} for a CurvesAsSubmobject whose submobjects have no points"
            )
        return submobjs_with_pts


class DashedVMobject(VMobject, metaclass=ConvertToOpenGL):
    """A :class:`VMobject` composed of dashes instead of lines.

    Parameters
    ----------
        vmobject
            The object that will get dashed
        num_dashes
            Number of dashes to add.
        dashed_ratio
            Ratio of dash to empty space.
        dash_offset
            Shifts the starting point of dashes along the
            path. Value 1 shifts by one full dash length.
        equal_lengths
            If ``True``, dashes will be (approximately) equally long.
            If ``False``, dashes will be split evenly in the curve's
            input t variable (legacy behavior).

    Examples
    --------
    .. manim:: DashedVMobjectExample
        :save_last_frame:

        class DashedVMobjectExample(Scene):
            def construct(self):
                r = 0.5

                top_row = VGroup()  # Increasing num_dashes
                for dashes in range(1, 12):
                    circ = DashedVMobject(Circle(radius=r, color=WHITE), num_dashes=dashes)
                    top_row.add(circ)

                middle_row = VGroup()  # Increasing dashed_ratio
                for ratio in np.arange(1 / 11, 1, 1 / 11):
                    circ = DashedVMobject(
                        Circle(radius=r, color=WHITE), dashed_ratio=ratio
                    )
                    middle_row.add(circ)

                func1 = FunctionGraph(lambda t: t**5,[-1,1],color=WHITE)
                func_even = DashedVMobject(func1,num_dashes=6,equal_lengths=True)
                func_stretched = DashedVMobject(func1, num_dashes=6, equal_lengths=False)
                bottom_row = VGroup(func_even,func_stretched)


                top_row.arrange(buff=0.3)
                middle_row.arrange()
                bottom_row.arrange(buff=1)
                everything = VGroup(top_row, middle_row, bottom_row).arrange(DOWN, buff=1)
                self.add(everything)

    """

    def __init__(
        self,
        vmobject: VMobject,
        num_dashes: int = 15,
        dashed_ratio: float = 0.5,
        dash_offset: float = 0,
        color: ManimColor = WHITE,
        equal_lengths: bool = True,
        **kwargs,
    ) -> None:
        self.dashed_ratio = dashed_ratio
        self.num_dashes = num_dashes
        super().__init__(color=color, **kwargs)
        r = self.dashed_ratio
        n = self.num_dashes
        if n > 0:
            # Assuming total length is 1
            dash_len = r / n
            if vmobject.is_closed():
                void_len = (1 - r) / n
            else:
                void_len = 1 - r if n == 1 else (1 - r) / (n - 1)

            period = dash_len + void_len
            phase_shift = (dash_offset % 1) * period

            if vmobject.is_closed():  # noqa: SIM108
                # closed curves have equal amount of dashes and voids
                pattern_len = 1
            else:
                # open curves start and end with a dash, so the whole dash pattern with the last void is longer
                pattern_len = 1 + void_len

            dash_starts = [((i * period + phase_shift) % pattern_len) for i in range(n)]
            dash_ends = [
                ((i * period + dash_len + phase_shift) % pattern_len) for i in range(n)
            ]

            # closed shapes can handle overflow at the 0-point
            # open shapes need special treatment for it
            if not vmobject.is_closed():
                # due to phase shift being [0...1] range, always the last dash element needs attention for overflow
                # if an entire dash moves out of the shape end:
                if dash_ends[-1] > 1 and dash_starts[-1] > 1:
                    # remove the last element since it is out-of-bounds
                    dash_ends.pop()
                    dash_starts.pop()
                elif dash_ends[-1] < dash_len:  # if it overflowed
                    if (
                        dash_starts[-1] < 1
                    ):  # if the beginning of the piece is still in range
                        dash_starts.append(0)
                        dash_ends.append(dash_ends[-1])
                        dash_ends[-2] = 1
                    else:
                        dash_starts[-1] = 0
                elif dash_starts[-1] > (1 - dash_len):
                    dash_ends[-1] = 1

            if equal_lengths:
                # calculate the entire length by adding up short line-pieces
                norms = np.array(0)
                for k in range(vmobject.get_num_curves()):
                    norms = np.append(norms, vmobject.get_nth_curve_length_pieces(k))
                # add up length-pieces in array form
                length_vals = np.cumsum(norms)
                ref_points = np.linspace(0, 1, length_vals.size)
                curve_length = length_vals[-1]
                self.add(
                    *(
                        vmobject.get_subcurve(
                            np.interp(
                                dash_starts[i] * curve_length,
                                length_vals,
                                ref_points,
                            ),
                            np.interp(
                                dash_ends[i] * curve_length,
                                length_vals,
                                ref_points,
                            ),
                        )
                        for i in range(len(dash_starts))
                    )
                )
            else:
                self.add(
                    *(
                        vmobject.get_subcurve(
                            dash_starts[i],
                            dash_ends[i],
                        )
                        for i in range(len(dash_starts))
                    )
                )
        # Family is already taken care of by get_subcurve
        # implementation
        if config.renderer == RendererType.OPENGL:
            self.match_style(vmobject, recurse=False)
        else:
            self.match_style(vmobject, family=False)
