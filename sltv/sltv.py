# -*- coding: utf-8 -*-
# Copyright (C) 2009 Holoscópio Tecnologia
# Author: Luciana Fujii Pontello <luciana@holoscopio.com>
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
from effects import *
from swap import *

import medialist

class Sltv:

    def __init__(self, preview_area, ui):
        self.player = None
        self.preview = Preview(preview_area)

        self.outputs = medialist.MediaList("Outputs", "output")
        self.outputs.load()

        self.sources = medialist.MediaList("Sources", "input")
        self.sources.load()

        self.audio = Audio()

        self.effect_enabled = "False"
        self.effect = {}
        self.effect_name = {}

        self.video_source = None
        self.audio_source = None

    def play(self, overlay_text, video_effect_name,
            audio_effect_name):

        self.player = gst.Pipeline("player")

        self.queue_video = gst.element_factory_make("queue", "queue_video")
        self.player.add(self.queue_video)

        audio_present = False

        # Source selection

        self.video_input_selector = gst.element_factory_make(
                "input-selector", "video_input_selector"
        )
        self.player.add(self.video_input_selector)
        self.source_pads = {}

        type = 0

        for row in self.sources.get_store():
            (name, source) = row
            element = source.create()

            if element.does_audio():
                if name == self.audio_source:
                    self.player.add(element)
                    self.queue_audio = gst.element_factory_make("queue", "queue_audio")
                    self.player.add(self.queue_audio)
                    pad = self.queue_audio.get_static_pad("sink")
                    element.audio_pad.link(pad)
                    audio_present = True
                elif element.does_video():

                        # If element does audio and video, it will be added.
                        # If audio is not chosen, it should be dropped

                        self.player.add(element)
                        fakesink = gst.element_factory_make("fakesink", None)
                        fakesink.set_property("silent", True)
                        fakesink.set_property("sync", False)
                        self.player.add(fakesink)
                        element.audio_pad.link(fakesink.get_static_pad("sink"))

            if element.does_video():
                if not element.does_audio():
                    self.player.add(element)
                self.source_pads[name] = \
                    self.video_input_selector.get_request_pad("sink%d")
                element.video_pad.link(self.source_pads[name])

            if name == self.video_source:
                type |= element.get_type()
            if name == self.audio_source:
                type |= element.get_type()

        self.video_input_selector.link(self.queue_video)
        self.video_input_selector.set_property(
                "active_pad", self.source_pads[self.video_source]
        )

        if self.effect_enabled:
            self.effect_name['video'] = video_effect_name
            self.effect_name['audio'] = audio_effect_name
        else:
            self.effect_name['video'] = "identity"
            self.effect_name['audio'] = "identity"
        self.effect['video'] = Effects.make_effect(
                self.effect_name['video'], "video"
        )
        self.player.add(self.effect['video'])

        self.overlay = gst.element_factory_make("textoverlay", "overlay")
        self.overlay.set_property("font-desc", "Sans Bold 14")
        self.player.add(self.overlay)

        gst.element_link_many(
                self.queue_video, self.effect['video'], self.overlay
        )

        self.preview_tee = gst.element_factory_make("tee", "tee")
        self.player.add(self.preview_tee)

        self.overlay.link(self.preview_tee)

        if audio_present:
            print "audio_present"
            self.convert = gst.element_factory_make("audioconvert", "convert")
            self.player.add(self.convert)

            self.effect['audio'] = Effects.make_effect(
                    self.effect_name['audio'], "audio"
            )
            self.player.add(self.effect['audio'])

            self.audio_tee = gst.element_factory_make("tee", "audio_tee")
            self.player.add(self.audio_tee)

            gst.element_link_many(
                    self.queue_audio, self.effect['audio'], self.convert,
                    self.audio_tee
            )

        for row in self.outputs.get_store():
            (name, output) = row

            queue_output = gst.element_factory_make("queue", None)
            self.player.add(queue_output)

            converter = output.create_converter()
            self.player.add(converter)

            encoder = output.create_encoding(type)
            self.player.add(encoder)

            sink = output.create()
            self.player.add(sink)

            gst.element_link_many(
                    self.preview_tee, queue_output, converter, encoder, sink
            )

            if audio_present:
                audio_queue = gst.element_factory_make("queue", None)
                self.player.add(audio_queue)

                gst.element_link_many(self.audio_tee, audio_queue, encoder)

        if self.preview_enabled:
            queue_preview = gst.element_factory_make("queue", "queue_preview")
            self.player.add(queue_preview)
            self.preview_element = self.preview.get_preview()
            self.player.add(self.preview_element)
            err = gst.element_link_many(self.preview_tee, queue_preview, self.preview_element)
            if err == False:
                print "Error conecting preview"

        self.overlay.set_property("text", overlay_text)

        bus = self.player.get_bus()
        bus.add_signal_watch()
        bus.enable_sync_message_emission()
        bus.connect("message", self.on_message)
        bus.connect("sync-message::element", self.on_sync_message)
        self.player.set_state(gst.STATE_PLAYING)

    def stop(self):
        self.player.send_event(gst.event_new_eos())

    def playing(self):
        return self.player and self.player.get_state()[1] == gst.STATE_PLAYING

    def set_effects(self, state):
        self.effect_enabled = state

        # If state is disabled and pipeline is playing, disable effects now

        if not self.effect_enabled:
            if self.playing():
                self.change_effect("identity", "video")
                self.change_effect("identity", "audio")

    def change_effect(self, effect_name, effect_type):
        if self.playing():
            print "PLAYING"
            Effects.change(
                    self.effect[effect_type], effect_name
            )
            self.effect_name[effect_type] = effect_name

    def switch_source(self):
        self.video_input_selector.set_property(
                "active-pad", self.source_pads[self.video_source]
        )

    def set_video_source(self, source_name):
        self.video_source = source_name
        if self.playing():
            self.switch_source()

    def set_audio_source(self, source_name):
        self.audio_source = source_name

    def set_preview(self, state):
        self.preview_enabled = state

    def change_overlay(self, overlay_text):
        self.overlay.set_property("text", overlay_text)

    def on_message(self, bus, message):
        t = message.type
        if t == gst.MESSAGE_EOS:
            self.player.set_state(gst.STATE_NULL)
        elif t == gst.MESSAGE_ERROR:
            (gerror, debug) = message.parse_error()
            print debug
            self.player.set_state(gst.STATE_NULL)

    def on_sync_message(self, bus, message):
        print "sync_message received"
        if message.structure is None:
            return
        message_name = message.structure.get_name()
        if message_name == "prepare-xwindow-id":
            previewsink = message.src
            self.preview.set_display(previewsink)
            previewsink.set_property("sync", "false")
            previewsink.set_property("force-aspect-ratio", "true")
