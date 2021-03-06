# -*- coding: utf-8 -*-
# Copyright (C) 2009 Holoscópio Tecnologia
# Author: Luciana Fujii Pontello <luciana@holoscopio.com>
# Author: Marcelo Jorge Vieira <metal@holoscopio.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.


import gobject
import pygst
pygst.require("0.10")
import gst
from audio import *
from preview import *
from swap import Swap
from videomixer import PictureInPicture

from registry import REGISTRY_INPUT, REGISTRY_OUTPUT, \
  REGISTRY_VIDEO_CONVERTER, REGISTRY_ENCODING, REGISTRY_AUDIO

import medialist
import effect
import volume
import metadata
import multeequeue
import outputbin
import audioinputbin

MEDIA_AUDIO = 1
MEDIA_VIDEO = 2

class Sltv(gobject.GObject):
    __gsignals__ = {
            "stopped": ( gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ()),
            "playing": ( gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ()),
            "preplay": ( gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ()),
            "error": (
                gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE,
                (gobject.TYPE_STRING,)
            ),
            "sync-message": (
                gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE,
                (gobject.type_from_name("GstBus"),
                    gobject.type_from_name("GstMessage"))
            ),
            "pipeline-ready": (gobject.SIGNAL_RUN_LAST, gobject.TYPE_NONE, ())
    }

    def __init__(self):
        gobject.GObject.__init__(self)

        self.player = None
        self.preview_enabled = False
        self.preview = None

        self.thumbnails = {}

        self.outputs = medialist.MediaList("Outputs", REGISTRY_OUTPUT)
        self.outputs.load()

        self.sources = medialist.MediaList("Sources", REGISTRY_INPUT)
        self.sources.load()

        self.audioconvs = medialist.MediaList("AudioConverters", REGISTRY_AUDIO)

        self.audioconvs.load()

        self.encoders = medialist.MediaList("Encoders", REGISTRY_ENCODING)
        self.encoders.load()

        self.videoconverters = medialist.MediaList(
                "VideoConverters", REGISTRY_VIDEO_CONVERTER
        )
        self.videoconverters.load()

        self.audio = Audio()

        self.effect_enabled = False
        self.effect = {}
        self.effect_name = {MEDIA_VIDEO: "identity", MEDIA_AUDIO: "identity"}

        self.video_source = None
        self.pip_source = None
        self.pip_position = None
        self.audio_source = None

        self.overlay_text = None
        self.overlay_font = "Sans Bold 14"
        self.valign = "baseline"
        self.halign = "center"
        self.volume = None
        self.volume_value = None

        self.pending_state = None
        self.watermark_location = None
        self.watermark_resize = False
        self.watermark_size = 1.0
        self.watermark_alpha = None
        self.watermark_selected = 0

        self.videobalance_contrast = None
        self.videobalance_brightness = None
        self.videobalance_hue = None
        self.videobalance_saturation = None

        self.input_type = 0
        self.output_bins = None

    def set_halign(self, halign):
        self.halign = halign
        if self.playing():
            self.overlay.set_property("halign", halign)

    def set_valign(self, valign):
        self.valign = valign
        if self.playing():
            self.overlay.set_property("valign", valign)

    def set_overlay_font(self, overlay_font):
        self.overlay_font = overlay_font
        if self.playing():
            self.overlay.set_property("font-desc", overlay_font)

    def set_metadata(self, taglist):
        self.taglist = taglist

    def get_thumbnail(self, name):
        return self.thumbnails[name]

    def set_overlay_text(self, overlay_text):
        self.overlay_text = overlay_text
        if self.playing():
            self.overlay.set_property("text", overlay_text)

    def set_watermark_location(self, location):
        self.watermark_location = location
        if self.playing():
            self.watermark.set_property("location", location)

    def set_watermark_resize(self, enabled):
        self.watermark_resize = enabled

    def set_watermark_size(self, size):
        self.watermark_size = size

    def set_watermark_alpha(self, alpha):
        self.watermark_alpha = alpha
        if self.playing():
            self.watermark.set_property("image-alpha", alpha)

    def _set_watermark(self, video_width, video_height):
        if self.watermark_location:
            self.watermark.set_property("location", self.watermark_location)

        if self.watermark_alpha:
            self.watermark.set_property("image-alpha", self.watermark_alpha)

        if self.watermark_resize:
            wm_width = self.watermark_size * video_width
            wm_height = self.watermark_size * video_height
            self.watermark.set_property("image-width", wm_width)
            self.watermark.set_property("image-height", wm_height)

    def set_videobalance_contrast(self, value):
        self.videobalance_contrast = value
        if self.playing():
            self.videobalance.set_property("contrast", value)

    def set_videobalance_brightness(self, value):
        self.videobalance_brightness = value
        if self.playing():
            self.videobalance.set_property("brightness", value)

    def set_videobalance_hue(self, value):
        self.videobalance_hue = value
        if self.playing():
            self.videobalance.set_property("hue", value)

    def set_videobalance_saturation(self, value):
        self.videobalance_saturation = value
        if self.playing():
            self.videobalance.set_property("saturation", value)

    def set_effect_name(self, effect_type, effect_name):
        if effect_name == "none":
            effect_name = "identity"
        self.effect_name[effect_type] = effect_name

    def stop_output(self, name):
        if not self.output_bins is None and self.output_bins.has_key(name):
            self.output_bins[name].stop()
            return True
        else:
            return False

    def play(self):

        self.emit("preplay")

        self.player = gst.Pipeline("player")

        self.queue_video = gst.element_factory_make("queue", "queue_video")
        self.player.add(self.queue_video)

        self.input_type = 0

        # Source selection

        self.source_pads = {}
        self.audio_pads = {}
        self.pip_pads = {}

        self.output_bins = {}
        type = 0
        source_number = 0
        pip_number = 0

        self.pip = PictureInPicture()

        self.player.add(self.pip)

        for row in self.sources.get_store():
            (name, source) = row
            element = source.create()
            self.player.add(element)

            if element.does_audio():
                if not self.input_type & MEDIA_AUDIO:

                    # The pipeline has audio sources, and this is the first
                    # audio source we add

                    if self.audio_source is None:
                        self.emit("error", "You need to select an audio source")
                        self.emit("stopped")
                        return
                    self.input_type |= MEDIA_AUDIO
                    self.input_selector = gst.element_factory_make(
                            "input-selector", "audio-selector"
                    )
                    self.player.add(self.input_selector)

                audiobin = audioinputbin.AudioInputBin(source)
                self.player.add(audiobin)

                element.audio_pad.link(audiobin.get_static_pad("sink"))
                self.audio_pads[name] = \
                        self.input_selector.get_request_pad("sink%d")
                audiobin.src_pad.link(self.audio_pads[name])

            if element.does_video():
                self.input_type |= MEDIA_VIDEO

                self.source_pads[name] = source_number
                source_number = source_number + 1

                # Thumbnail preview

                tee = gst.element_factory_make("tee", None)
                self.player.add(tee)
                element.video_pad.link(tee.sink_pads().next())

                thumbnail_queue = gst.element_factory_make("queue", None)
                self.player.add(thumbnail_queue)
                self.thumbnails[name] = Preview(self)
                self.player.add(self.thumbnails[name])

                thumbnail_err = gst.element_link_many(
                    tee, thumbnail_queue, self.thumbnails[name]
                )
                if thumbnail_err == False:
                    self.emit("error", "Error conecting thumbnail preview.")

                # Picture in Picture

                self.pip_pads[name] = pip_number
                pip_number = pip_number + 1

                main_queue = gst.element_factory_make("queue", None)
                self.player.add(main_queue)
                pip_queue = gst.element_factory_make("queue", None)
                self.player.add(pip_queue)

                tee.link(main_queue)
                tee.link(pip_queue)
                main_queue.src_pads().next().link(self.pip.get_request_pad_A())
                pip_queue.src_pads().next().link(self.pip.get_request_pad_B())

            if name == self.video_source:
                type |= element.get_type()
            if name == self.audio_source:
                type |= element.get_type()

        self.watermark = gst.element_factory_make(
                "cairoimageoverlay", "cairoimageoverlay"
        )
        self.player.add(self.watermark)

        self.colorspace = gst.element_factory_make(
                "ffmpegcolorspace", "colorspace-imageoverlay-videobalance"
        )
        self.player.add(self.colorspace)

        self.videobalance = gst.element_factory_make(
                "videobalance", "videobalance"
        )
        self.player.add(self.videobalance)
        if self.videobalance_contrast:
            self.videobalance.set_property(
                    "contrast", self.videobalance_contrast
            )
        if self.videobalance_brightness:
            self.videobalance.set_property(
                    "brightness", self.videobalance_brightness
            )
        if self.videobalance_hue:
            self.videobalance.set_property(
                    "hue", self.videobalance_hue
            )
        if self.videobalance_saturation:
            self.videobalance.set_property(
                    "saturation", self.videobalance_saturation
            )

        gst.element_link_many(
                self.pip, self.watermark, self.colorspace, self.videobalance,
                self.queue_video
        )

        self._switch_source()
        self._switch_pip()

        if self.pip_position:
            self.pip.set_property("position", self.pip_position)

        self.effect[MEDIA_VIDEO] = effect.video_effect.VideoEffect(
                self.effect_name[MEDIA_VIDEO]
        )
        self.player.add(self.effect[MEDIA_VIDEO])

        self.overlay = gst.element_factory_make("textoverlay", "overlay")
        self.overlay.set_property("font-desc", self.overlay_font)
        self.overlay.set_property("halign", self.halign)
        self.overlay.set_property("valign", self.valign)
        self.player.add(self.overlay)

        gst.element_link_many(
                self.queue_video, self.effect[MEDIA_VIDEO], self.overlay
        )

        self.preview_tee = multeequeue.MulTeeQueue()
        self.player.add(self.preview_tee)

        self.overlay.link(self.preview_tee)

        if self.input_type & MEDIA_AUDIO:
            self.convert = gst.element_factory_make("audioconvert", "convert")
            self.player.add(self.convert)

            self.effect[MEDIA_AUDIO] = effect.audio_effect.AudioEffect(
                    self.effect_name[MEDIA_AUDIO]
            )
            self.player.add(self.effect[MEDIA_AUDIO])

            self.audio_tee = gst.element_factory_make("tee", "audio_tee")
            self.player.add(self.audio_tee)

            self.volume = volume.Volume()
            self.player.add(self.volume)

            gst.element_link_many(
                    self.input_selector, self.volume,
                    self.effect[MEDIA_AUDIO], self.convert, self.audio_tee
            )
            self.input_selector.set_property(
                    "active-pad", self.audio_pads[self.audio_source]
            )
        added_encoders = {}

        pip_width = 0
        pip_height = 0

        for row in self.outputs.get_store():
            (name, output) = row

            output_bin = outputbin.OutputBin(output)
            self.output_bins[name] = output_bin
            self.player.add(output_bin)

            encoder_name = output.get_config()["parent"]

            encoder_item = self.encoders.get_item(encoder_name)
            if encoder_item is None:
                self.emit("error", "Please, add an encoder.")
                break

            if added_encoders.has_key(encoder_name):
                tee = added_encoders[encoder_name]

                tee.link(output_bin)
            else:
                tee = gst.element_factory_make("tee", None)
                self.player.add(tee)

                converter_item = encoder_item.parent
                converter = converter_item.create()
                if converter_item.config["width"] > pip_width:
                    pip_width = converter_item.config["width"]
                if converter_item.config["height"] > pip_height:
                    pip_height = converter_item.config["height"]
                self.player.add(converter)

                encoder = encoder_item.factory.create(type)
                if encoder.vorbisenc:
                    self.metadata = metadata.Metadata(encoder.vorbisenc)
                    self.metadata.set_tags(self.taglist)
                encoder.config(encoder_item.config)
                self.player.add(encoder)

                added_encoders[encoder_name] = tee
                self.preview_tee.get_src_pad().link(
                        converter.sink_pads().next()
                )
                gst.element_link_many(
                        converter, encoder, tee, output_bin
                )

                if self.input_type & MEDIA_AUDIO:
                    audio_queue = gst.element_factory_make("queue", None)
                    self.player.add(audio_queue)

                    gst.element_link_many(self.audio_tee, audio_queue, encoder)

        if self.preview_enabled:
            self.preview = Preview(self)
            self.player.add(self.preview)
            self.preview_tee.get_src_pad().link(self.preview.sink_pads().next())

        if pip_width == 0:
            pip_width = 320
            pip_height = 240
        self.pip.set_property("width", int(pip_width))
        self.pip.set_property("height", int(pip_height))

        self.video_width = int(pip_width)
        self.video_height = int(pip_height)
        self._set_watermark(self.video_width, self.video_height)

        self.overlay.set_property("text", self.overlay_text)
        if self.volume_value is not None:
            self.volume.set_property("volume", self.volume_value)

        self.emit("pipeline-ready")

        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.enable_sync_message_emission()
        bus.connect("message", self.on_message)
        bus.connect("sync-message::element", self.on_sync_message)
        cr = self.player.set_state(gst.STATE_PLAYING)
        if cr == gst.STATE_CHANGE_SUCCESS:
            self.emit("playing")
        elif cr == gst.STATE_CHANGE_ASYNC:
            self.pending_state = gst.STATE_PLAYING

    def stop(self):
        cr = self.player.set_state(gst.STATE_NULL)
        if cr == gst.STATE_CHANGE_SUCCESS:
            self.emit("stopped")
        elif cr == gst.STATE_CHANGE_ASYNC:
            self.pending_state = gst.STATE_NULL

    def playing(self):
        return self.player and self.player.get_state()[1] == gst.STATE_PLAYING

    def _swap_effect(self, effect_type):
            if effect_type == MEDIA_VIDEO:
                new_effect = effect.video_effect.VideoEffect(
                        self.effect_name[effect_type]
                )
                Swap.swap_element(
                        self.player, self.queue_video, self.overlay,
                        self.effect[effect_type], new_effect
                )
                self.effect[effect_type] = new_effect
            else:
                new_effect = effect.audio_effect.AudioEffect(
                        self.effect_name[effect_type]
                )
                Swap.swap_element(
                        self.player, self.volume, self.convert,
                        self.effect[effect_type], new_effect
                )
                self.effect[effect_type] = new_effect

    def set_effects(self, state):

        self.effect_enabled = state

        # If state is disabled and pipeline is playing, disable effects now

        if not self.effect_enabled:
            if self.playing():
                self.change_effect("identity", MEDIA_VIDEO)
                self.change_effect("identity", MEDIA_AUDIO)

    def change_effect(self, effect_name, effect_type):

        # If that input doesn't exist, then there is no effect to change.

        if not self.input_type & effect_type:
            return

        if self.playing():
            self.set_effect_name(effect_type, effect_name)
            self._swap_effect(effect_type)

    def _switch_source(self):
        self.pip.set_property(
                "a-active", self.source_pads[self.video_source]
        )

    def set_video_source(self, source_name):
        self.video_source = source_name
        if self.playing():
            self._switch_source()

    def _switch_pip(self):
        if self.pip_source and self.pip_pads.has_key(self.pip_source):
            self.pip.set_property("enabled", True)
            self.pip.set_property(
                    "b-active", self.pip_pads[self.pip_source]
            )
        else:
            self.pip.set_property("enabled", False)

    def set_pip_source(self, source_name):
        self.pip_source = source_name
        if self.playing():
            self._switch_pip()

    def set_pip_position(self, selected):
        self.pip_position = selected
        if self.playing():
            self.pip.set_property("position", selected)

    def set_audio_source(self, source_name):
        self.audio_source = source_name
        if self.playing():
            self.input_selector.set_property(
                    "active-pad", self.audio_pads[source_name]
            )

    def set_preview(self, state):
        self.preview_enabled = state

    def get_preview(self):
        return self.preview

    def set_volume(self, value):
        self.volume_value = value
        if self.volume:
            self.volume.set_property("volume", value)

    def on_message(self, bus, message):
        t = message.type
        if t == gst.MESSAGE_EOS:
            cr = self.player.set_state(gst.STATE_NULL)
            if cr == gst.STATE_CHANGE_SUCCESS:
                self.emit("stopped")
            elif cr == gst.STATE_CHANGE_ASYNC:
                self.pending_state = gst.STATE_NULL
        elif t == gst.MESSAGE_ERROR:
            (gerror, debug) = message.parse_error()
            self.emit("error", gerror.message)
            print debug
            cr = self.player.set_state(gst.STATE_NULL)
            if cr == gst.STATE_CHANGE_SUCCESS:
                self.emit("stopped")
            elif cr == gst.STATE_CHANGE_ASYNC:
                self.pending_state = gst.STATE_NULL
        elif t == gst.MESSAGE_ASYNC_DONE:
            if self.pending_state == gst.STATE_NULL:
                self.emit("stopped")
            elif self.pending_state == gst.STATE_PLAYING:
                self.emit("playing")
            self.pending_state = None

    def on_sync_message(self, bus, message):
        self.emit("sync-message", bus, message)
