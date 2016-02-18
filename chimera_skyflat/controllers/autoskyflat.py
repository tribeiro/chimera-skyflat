from __future__ import division
import os
from astropy.io import fits
from chimera.controllers.imageserver.util import getImageServer
from chimera.core.exceptions import ChimeraException
from chimera.interfaces.camera import Shutter
from chimera.util.coord import Coord
from chimera.util.image import ImageUtil
from chimera.util.position import Position
import time
from datetime import datetime, timedelta
from chimera_skyflat.interfaces.autoskyflat import IAutoSkyFlat

import ntpath

import numpy as np

__author__ = 'kanaan'

from chimera.core.chimeraobject import ChimeraObject


class AutoSkyFlat(ChimeraObject, IAutoSkyFlat):
    # normal constructor
    # initialize the relevant variables
    def __init__(self):
        ChimeraObject.__init__(self)

    def _getSite(self):
        return self.getManager().getProxy(self["site"])

    def _getTel(self):
        return self.getManager().getProxy(self["telescope"])

    def _getCam(self):
        return self.getManager().getProxy(self["camera"])

    def _getFilterWheel(self):
        return self.getManager().getProxy(self["filterwheel"])

    def _takeImage(self, exptime, filter):

        cam = self._getCam()
        if self["filterwheel"] is not None:
            fw = self._getFilterWheel()
            fw.setFilter(filter)
        self.log.debug("Start frame")
        frames = cam.expose(exptime=exptime, frames=1, shutter=Shutter.OPEN,
                            filename=os.path.basename(ImageUtil.makeFilename("skyflat-$DATE")))
        self.log.debug("End frame")

        if frames:
            image = frames[0]
            image_path = image.filename()
            if not os.path.exists(image_path):  # If image is on a remote server, donwload it.

                #  If remote is windows, image_path will be c:\...\image.fits, so use ntpath instead of os.path.
                if ':\\' in image_path:
                    modpath = ntpath
                else:
                    modpath = os.path
                image_path = ImageUtil.makeFilename(os.path.join(getImageServer(self.getManager()).defaultNightDir(),
                                                                 modpath.basename(image_path)))
                t0 = time.time()
                self.log.debug('Downloading image from server to %s' % image_path)
                if not ImageUtil.download(image, image_path):
                    raise ChimeraException('Error downloading image %s from %s' % (image_path, image.http()))
                self.log.debug('Finished download. Took %3.2f seconds' % (time.time() - t0))
            return image_path, image
        else:
            raise Exception("Could not take an image")

    def _moveScope(self):
        """
        Moves the scope, usually to zenith
        """
        tel = self._getTel()
        try:
            self.log.debug("Skyflat Slewing scope to zenith")
            tel.slewToAltAz(Position.fromAltAz(self["flat_alt"], self["flat_az"]))
        except:
            self.log.debug("Error moving the telescope")

    def _stopTracking(self):
        """
        stops tracking the telescope
        """
        tel = self._getTel()
        try:
            self.log.debug("Skyflat is stopping telescope tracking")
            tel.stopTracking()
        except:
            self.log.debug("Error stopping the telescope")

    def _startTracking(self):
        """
        stops tracking the telescope
        """
        tel = self._getTel()
        try:
            self.log.debug("Skyflat is restarting telescope tracking")
            tel.startTracking()
        except:
            self.log.debug("Error starting the telescope")

    def getFlats(self, debug=False):
        """
        DOCME
        :type self: object
        :param sun_alt_hi:
        :param sun_alt_low:
        :return:
        """
        # 1 - Wait for the Sun to enter the altitude strip where we can take skyflats
        # 2 - Take first image to guess exponential scaling factor
        # 3 - Measure exponential scaling factor
        # 4 - Compute exposure time
        # 5 - Take flat
        # 6 - Goto 2.
        site = self._getSite()
        pos = site.sunpos()
        # self.log.debug('Sun altitude is %f %f' % pos.alt % self.sun_alt_hi)
        self.log.debug(
            'Starting sky flats Sun altitude is {} {} {}'.format(pos.alt.D, self["sun_alt_hi"], self["sun_alt_low"]))

        # while the Sun is above or below the flat field strip we just wait
        while pos.alt.D > self["sun_alt_hi"] or pos.alt.D < self["sun_alt_low"]:
            # maybe we should test for pos << self.sun_alt_hi K???
            time.sleep(10)
            pos = site.sunpos()
            self.log.debug('Sun altitude is %f waiting to be between %f and %f' % (
            pos.alt.D, self["sun_alt_hi"], self["sun_alt_low"]))

        # now the Sun is in the skyflat altitude strip
        self.log.debug('Taking image...')
        sky_level = self.getSkyLevel(exptime=self["defaultExptime"])
        self.log.debug('Mean: %f' % sky_level)
        pos = site.sunpos()
        self._moveScope()  # now that the skyflat time arrived move the telescope
        self._stopTracking()
        while self["sun_alt_hi"] > pos.alt.D > self["sun_alt_low"]:  # take flats until the Sun is out of the skyflats regions
            self.log.debug("Initial positions {} {} {}".format(pos.alt.D, self["sun_alt_hi"], self["sun_alt_low"]))
            expTime = self.computeSkyFlatTime(sky_level)
            self.log.debug('Taking sky flat image with exptime = %f' % expTime)
            sky_level = self.getSkyLevel(exptime=expTime)
            self.log.debug('Done taking image, average counts = %f' % sky_level)
            pos = site.sunpos()
            self.log.debug("{} {} {}".format(pos.alt.D,self["sun_alt_hi"],self["sun_alt_low"]))


        self._startTracking()

    def computeSkyFlatTime(self, sky_level):
        """
        User specifies:
        :param sky_level - current level of sky counts
        :param pos.alt - initial Sun sun_altitude
        :return exposure time needed to reach sky_level
        Method returns exposureTime
        This computation requires Scale, Slope, Bias
        Scale and Slope are filter dependent, I think they should be part of some enum type variable
        """

        site = self._getSite()
        intCounts = 0.0
        exposure_time = 0
        initialTime = site.ut()
        sun_altitude = site.sunpos().alt
        while 1:
            sky_counts = self.expArg(sun_altitude.R, self["Scale"], self["Slope"], self["Bias"])
            initialTime = initialTime + timedelta(seconds=1)
            sun_altitude = site.sunpos(initialTime).alt
            self.log.debug(
                "Computing exposure time {} sky_counts = {} intCounts = {}".format(initialTime, sky_counts, intCounts))
            if intCounts + sky_counts >= self["idealCounts"]:
                self.log.debug("Breaking the Exposure Time Calculation loop. Exposure time {} Computed counts {}"
                               .format(exposure_time,intCounts))
                return float(exposure_time)
            exposure_time += 1
            intCounts += sky_counts

        return float(exposure_time)

    def expTime(self, sky_level, altitude):
        """
        Computes sky level for current altitude
        Assumes sky_lve
        :param sky_level:
        :param altitude:
        :return:
        """

    def getSkyLevel(self, exptime, debug=False):
        """
        Takes one sky flat and returns average
        Need to get rid of deviant points, mode is best, but takes too long, using mean for now K???
        For now just using median which is almost OK
        """

        self.setSideOfPier("E")  # self.sideOfPier)
        try:
            filename, image = self._takeImage(exptime=exptime, filter=self["filter"])
            frame = fits.getdata(filename)
            img_mean = np.mean(frame)
            image.close()
            return (img_mean)
        except:
            self.log.error("Can't take image")
            raise

    def expArg(self, x, Scale, Slope, Bias):
        # print "expArg", x, Scale, Slope, Bias
        return (Scale * np.exp(Slope * x) + Bias)
