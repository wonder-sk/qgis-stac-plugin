# -*- coding: utf-8 -*-
"""
    Assets dialog, shows all the available assets.
"""

import os

from pathlib import Path
from osgeo import ogr

from functools import partial

from qgis import processing

from qgis.PyQt import QtCore, QtGui, QtWidgets
from qgis.PyQt.uic import loadUiType

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsMapLayer,
    QgsNetworkContentFetcherTask,
    QgsProject,
    QgsProcessing,
    QgsProcessingFeedback,
    QgsRasterLayer,
    QgsTask,
    QgsVectorLayer,

)

from ..resources import *

from ..api.models import (
    AssetLayerType,
)

from ..conf import (
    Settings,
    settings_manager
)

from .asset_widget import AssetWidget

from ..utils import log, tr

DialogUi, _ = loadUiType(
    os.path.join(os.path.dirname(__file__), "../ui/item_assets_widget.ui")
)


class AssetsDialog(QtWidgets.QDialog, DialogUi):
    """ Dialog for adding and downloading STAC Item assets"""

    def __init__(
            self,
            item,
            parent,
            main_widget
    ):
        """ Constructor

        :param item: Item object with assets that are to be shown.
        :type item: model.Item

        :param parent Parent widget
        :type parent: QWidget

        :param main_widget: Plugin main widget
        :type main_widget: QWidget
        """
        super().__init__()
        self.setupUi(self)
        self.item = item
        self.assets = item.assets
        self.parent = parent
        self.main_widget = main_widget
        self.cog_string = '/vsicurl/'
        self.download_result = {}

        self.prepare_assets()

    def prepare_assets(self):
        """ Loads the dialog with the list of assets.
        """

        if len(self.assets) > 0:
            self.title.setText(
                tr("Item {}, has {} asset(s) available").
                format(self.item.id, len(self.assets))
            )
        else:
            self.title.setText(
                tr("Item {} has no assets").
                format(len(self.item.id))
            )

        scroll_container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(1)
        for asset in self.assets:
            asset_widget = AssetWidget(asset, self)

            layout.addWidget(asset_widget)
            layout.setAlignment(asset_widget, QtCore.Qt.AlignTop)
        vertical_spacer = QtWidgets.QSpacerItem(
            20,
            40,
            QtWidgets.QSizePolicy.Minimum,
            QtWidgets.QSizePolicy.Expanding
        )
        layout.addItem(vertical_spacer)
        scroll_container.setLayout(layout)
        self.scroll_area.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarAlwaysOff
        )
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(scroll_container)

    def update_inputs(self, enabled):
        """ Updates the inputs widgets state in the main search item widget.

        :param enabled: Whether to enable the inputs or disable them.
        :type enabled: bool
        """
        self.scroll_area.setEnabled(enabled)
        self.parent.update_inputs(enabled)

    def download_asset(self, asset, load_asset=False):
        """ Downloads the passed asset into directory defined in the plugin settings.

        :param asset: Item asset
        :type asset: models.ResourceAsset

        :param load_asset: Whether to load an asset after download has finished.
        :type load_asset: bool
        """
        download_folder = settings_manager.get_value(
            Settings.DOWNLOAD_FOLDER
        )
        item_folder = os.path.join(download_folder, self.item.id) \
            if download_folder else None
        feedback = QgsProcessingFeedback()
        try:
            if item_folder:
                os.mkdir(item_folder)
        except FileExistsError as fe:
            pass
        except FileNotFoundError as fn:
            self.main_widget.show_message(
                tr("Folder {} is not found").format(download_folder),
                Qgis.Critical
            )
            return
        except PermissionError as pe:
            self.main_widget.show_message(
                tr("Permission error writing in download folder"),
                Qgis.Critical
            )
            return

        url = asset.href
        extension = Path(url).suffix
        extension_suffix = extension.split('?')[0] if extension else ""
        title = f"{asset.title}{extension_suffix}"

        title = self.clean_filename(title)

        output = os.path.join(
            item_folder, title
        ) if item_folder else QgsProcessing.TEMPORARY_OUTPUT
        params = {'URL': url, 'OUTPUT': output}

        self.download_result["file"] = output

        layer_types = [
            AssetLayerType.COG.value,
            AssetLayerType.GEOTIFF.value,
            AssetLayerType.NETCDF.value,
        ]
        try:
            self.main_widget.show_message(
                tr("Download for file {} to {} has started."
                   ).format(
                    title,
                    item_folder
                ),
                level=Qgis.Info
            )
            self.main_widget.show_progress(
                f"Downloading {url}",
                minimum=0,
                maximum=100,
            )

            feedback.progressChanged.connect(
                self.main_widget.update_progress_bar
            )
            feedback.progressChanged.connect(self.download_progress)

            # After asset download has finished, load the asset
            # if it can be loaded as a QGIS map layer.
            if load_asset and asset.type in layer_types:
                asset.href = self.download_result["file"]
                asset.title = title
                asset.type = AssetLayerType.GEOTIFF.value \
                    if AssetLayerType.COG.value in asset.type else asset.type
                load_file = partial(self.load_file_asset, asset)
                feedback.progressChanged.connect(load_file)

            processing.run(
                "qgis:filedownloader",
                params,
                feedback=feedback
            )

        except Exception as e:
            self.main_widget.show_message(
                tr("Error in downloading file, {}").format(str(e))
            )

    def load_file_asset(self, asset, value):
        """Loads the passed asset into QGIS map canvas if the
        progress value indicates the download has finished.

        param asset: Item asset
        :type asset: models.ResourceAsset

        :param value: Download progress value
        :type value: int
        """
        if value == 100:
            self.load_asset(asset)

    def download_progress(self, value):
        """Tracks the download progress of value and updates
        the info message when the download has finished

        :param value: Download progress value
        :type value: int
        """
        if value == 100:
            self.main_widget.show_message(
                tr("Download for file {} has finished."
                   ).format(
                    self.download_result["file"]
                ),
                level=Qgis.Info
            )

    def clean_filename(self, filename):
        """ Creates a safe filename by removing operating system
        invalid filename characters.

        :param filename: File name
        :type filename: str
        """
        characters = " %:/,\[]<>*?"

        for character in characters:
            if character in filename:
                filename = filename.replace(character, '_')

        return filename


    def load_asset(self, asset):
        """ Loads asset into QGIS.
            Checks if the asset type is a loadable layer inside QGIS.

        :param asset: Item asset
        :type asset: models.ResourceAsset
        """

        assert_type = asset.type
        raster_types = ','.join([
            AssetLayerType.COG.value,
            AssetLayerType.GEOTIFF.value,
            AssetLayerType.NETCDF.value
        ])
        vector_types = ','.join([
            AssetLayerType.GEOJSON.value,
            AssetLayerType.GEOPACKAGE.value
        ])

        if assert_type in raster_types:
            layer_type = QgsMapLayer.RasterLayer
        elif assert_type in vector_types:
            layer_type = QgsMapLayer.VectorLayer

        if AssetLayerType.COG.value in assert_type:
            asset_href = f"{self.cog_string}" \
                         f"{asset.href}"
        else:
            asset_href = f"{asset.href}"
        asset_name = asset.title

        self.update_inputs(False)
        self.layer_loader = LayerLoader(
            asset_href,
            asset_name,
            layer_type
        )

        # Using signal approach to detect the results of the layer loader
        # task as the callback function approach doesn't make the task
        # to recall the assigned callbacks in the provided context.
        self.layer_loader.taskCompleted.connect(self.add_layer)
        self.layer_loader.progressChanged.connect(self.main_widget.update_progress_bar)
        self.layer_loader.taskTerminated.connect(self.layer_loader_terminated)

        QgsApplication.taskManager().addTask(self.layer_loader)

        self.main_widget.show_progress(
            f"Adding asset \"{asset_name}\" into QGIS",
            minimum=0,
            maximum=100,
        )
        self.main_widget.update_progress_bar(0)
        log(tr("Started adding asset into QGIS"))

    def add_layer(self):
        """ Adds layer into the current QGIS project.
            For the layer to be added successfully, the task for loading
            layer need to exist and the corresponding layer need to be
            available.
        """
        if self.layer_loader and self.layer_loader.layer:
            layer = self.layer_loader.layer
            QgsProject.instance().addMapLayer(layer)

            message = tr("Sucessfully added asset as a map layer")
            level = Qgis.Info
        elif self.layer_loader and self.layer_loader.layers:
            layers = self.layer_loader.layers
            for layer in layers:
                QgsProject.instance().addMapLayer(layer)

            message = tr("Sucessfully added asset as a map layer(s)")
            level = Qgis.Info
        elif self.layer_loader and self.layer_loader.error:
            message = self.layer_loader.error
            level = Qgis.Critical
        else:
            message = tr("Problem fetching asset and loading it, into QGIS")
            level = Qgis.Critical

        self.update_inputs(True)
        log(message)
        self.main_widget.show_message(
            message,
            level=level
        )

    def layer_loader_terminated(self):
        """ Shows message to user when layer loading task has been terminated"""
        message = tr("QGIS background task for loading assets was terminated.")
        self.update_inputs(True)
        log(message)
        self.main_widget.show_message(
            message,
            level=Qgis.Critical
        )

    def handle_layer_error(self, message):
        """ Handles the error message from the layer loading task

        :param message: The error message
        :type message: str
        """
        self.update_inputs(True)
        log(message)
        self.main_widget.show_message(
            message
        )


class LayerLoader(QgsTask):
    """ Prepares and loads items as assets inside QGIS as layers."""
    def __init__(
        self,
        layer_uri,
        layer_name,
        layer_type
    ):

        super().__init__()
        self.layer_uri = layer_uri
        self.layer_name = layer_name
        self.layer_type = layer_type
        self.error = None
        self.layer = None
        self.layers = []

    def run(self):
        """ Operates the main layers loading logic
        """
        log(
            tr("Fetching layers in a background task.")
        )
        if self.layer_type is QgsMapLayer.RasterLayer:
            self.layer = QgsRasterLayer(
                self.layer_uri,
                self.layer_name
            )
            return self.layer.isValid()
        elif self.layer_type is QgsMapLayer.VectorLayer:
            extension = Path(self.layer_uri).suffix
            result = False

            if extension is not ".gpkg":
                self.layer = QgsVectorLayer(
                    self.layer_uri,
                    self.layer_name,
                    AssetLayerType.VECTOR.value
                )
                result = self.layer.isValid()
            else:
                gpkg_connection = ogr.Open(self.layer_uri)

                for layer_item in gpkg_connection:
                    layer = QgsVectorLayer(
                        f"{gpkg_connection}|layername={layer_item.GetName()}",
                        layer_item.GetName(),
                        AssetLayerType.VECTOR.value
                    )
                    self.layers.append(layer.clone())
                    # If any layer from the geopackage is valid, load it.
                    if layer.isValid():
                        self.layer = layer
                        result = True
            return result
        else:
            raise NotImplementedError

        return False

    def finished(self, result: bool):
        """ Calls the handler responsible for adding the
         layer into QGIS project.

        :param result: Whether the run() operation finished successfully
        :type result: bool
        """
        if result and self.layer:
            log(
                f"Fetched layer with URI "
                f"{self.layer_uri} "
            )
            # Due to the way QGIS is handling layers sharing between tasks and
            # the main thread, sending the layer to the main thread
            # without cloning it can lead to unpredicted crashes,
            # hence we clone the layer before storing it, so it can
            # be used in the main thread.
            self.layer = self.layer.clone()
        else:
            provider_error = tr("error {}").format(
                self.layer.dataProvider().error()
            )if self.layer else ""
            self.error = tr(
                f"Couldn't load layer "
                f"{self.layer_uri},"
                f"{provider_error}"
            )
            log(
                self.error
            )
