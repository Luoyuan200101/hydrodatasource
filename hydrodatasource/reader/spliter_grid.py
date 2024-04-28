import os

import geopandas as gpd
import h5py
import numpy as np
import pandas as pd
import xarray as xr
from pandas.core.indexes.api import default_index
import hydrodatasource.processor.mask as hpm
import hydrodatasource.configs.config as hdscc
from hydrodatasource.processor.basin_mean_rainfall import rainfall_average
from hydrodatasource.reader import access_fs


def query_path_from_metadata(basin_id, time_start=None, time_end=None, bbox=None, data_source='gpm'):
    # query path from other columns from metadata.csv
    metadata_df = pd.read_csv(f's3://grids-origin/{data_source}_metadata.csv', storage_options=hdscc.MINIO_PARAM)
    source_list = []
    if data_source == 'gpm':
        start_end_len = len(pd.date_range(time_start, time_end, freq='30min').to_list())
    elif data_source == 'smap':
        start_end_len = len(pd.date_range(time_start, time_end, freq='3h').to_list())
    elif (data_source == 'gfs') | (data_source == 'era5_land') | (data_source == 'era5'):
        start_end_len = len(pd.date_range(time_start, time_end, freq='h').to_list())
    else:
        start_end_len = len(pd.date_range(time_start, time_end, freq='d').to_list())
    if data_source == 'gpm':
        source_list = [x for x in metadata_df['path'] if 'GPM' in x]
    elif data_source == 'smap':
        source_list = [x for x in metadata_df['path'] if 'SMAP' in x]
    elif data_source == 'gfs':
        source_list = [x for x in metadata_df['path'] if 'GFS' in x]
    elif data_source == 'era5_land':
        source_list = [x for x in metadata_df['path'] if 'era5_land' in x]
    elif data_source == 'era5':
        source_list = [x for x in metadata_df['path'] if 'era5' in x]
    paths = metadata_df[metadata_df['path'].isin(source_list)]
    if time_start is not None:
        paths = paths[paths['time_start'] >= time_start]
    if time_end is not None:
        paths = paths[paths['time_end'] <= time_end]
    if (data_source == 'gpm') or (data_source == 'smap') or (data_source == 'era5_land') or (data_source == 'era5'):
        if bbox is not None:
            paths = paths[
                (paths['bbox'].apply(lambda x: string_to_list(x)[0] <= bbox[0])) &
                (paths['bbox'].apply(lambda x: string_to_list(x)[1] >= bbox[1])) &
                (paths['bbox'].apply(lambda x: string_to_list(x)[2] >= bbox[2])) &
                (paths['bbox'].apply(lambda x: string_to_list(x)[3] <= bbox[3]))]
    elif data_source == 'gfs':
        path_list_predicate = paths[paths['path'].isin(choose_gfs(paths, time_start, time_end))]
        paths = paths[path_list_predicate['bbox'].apply(lambda x: string_to_list(x)[0] <= bbox[0]) &
                      (paths['bbox'].apply(lambda x: string_to_list(x)[1] >= bbox[1])) &
                      (paths['bbox'].apply(lambda x: string_to_list(x)[2] >= bbox[2])) &
                      (paths['bbox'].apply(lambda x: string_to_list(x)[3] <= bbox[3]))]
    candidate_tile_list = paths[paths['path'].apply(lambda x: ('_tile' in x) & (basin_id in x))]
    tile_list = candidate_tile_list['path'][candidate_tile_list['bbox'].apply(lambda x: str(bbox) == x)].to_list()
    if len(tile_list) < start_end_len - 1:
        for path in paths['path']:
            if data_source == 'smap':
                path_ds = h5py.File(hdscc.FS.open(path))
                # datetime.fromisoformat('2000-01-01T12:00:00') + timedelta(seconds=path_ds['time'][0])
                lon_array = path_ds['cell_lon'][0]
                lat_array = path_ds['cell_lat'][:, 0]
                cell_lon_w = np.argwhere(lon_array >= bbox[0])[0][0]
                cell_lon_e = np.argwhere(lon_array <= bbox[1])[-1][0]
                cell_lat_n = np.argwhere(lat_array <= bbox[2])[0][0]
                cell_lat_s = np.argwhere(lat_array >= bbox[3])[-1][0]
                tile_da = path_ds['Geophysical_Data']['sm_surface'][cell_lat_n: cell_lat_s + 1,
                          cell_lon_w: cell_lon_e + 1]
                tile_ds = xr.DataArray(tile_da).to_dataset(name='sm_surface')
            elif (data_source == 'era5_land') | (data_source == 'era5'):
                path_ds = access_fs.spec_path(path.lstrip('s3://'), head='minio')
                tile_ds = path_ds.sel(time=slice(time_start, time_end), longitude=slice(bbox[0], bbox[1]),
                                      latitude=slice(bbox[2], bbox[3]))
            else:
                # 会扰乱桶，注意
                path_ds = xr.open_dataset(hdscc.FS.open(path))
                if data_source == 'gpm':
                    tile_ds = path_ds.sel(time=slice(time_start, time_end), lon=slice(bbox[0], bbox[1]),
                                          lat=slice(bbox[3], bbox[2]))
                # 会扰乱桶，注意
                elif data_source == 'gfs':
                    tile_ds = path_ds.sel(time=slice(time_start, time_end), longitude=slice(bbox[0], bbox[1]),
                                          latitude=slice(bbox[2], bbox[3]))
                else:
                    tile_ds = path_ds
            tile_path = path.rstrip('.nc4') + f'{basin_id}_tile.nc4'
            tile_list.append(tile_path)
            if data_source == 'gfs':
                temp_df = pd.DataFrame(
                    {'bbox': str(bbox), 'time_start': time_start, 'time_end': time_end, 'res_lon': 0.25,
                     'res_lat': 0.25,
                     'path': tile_path}, index=default_index(1))
            elif data_source == 'smap':
                temp_df = pd.DataFrame(
                    {'bbox': str(bbox), 'time_start': time_start, 'time_end': time_end, 'res_lon': 0.08,
                     'res_lat': 0.08,
                     'path': tile_path}, index=default_index(1))
            elif (data_source == 'gpm') or (data_source == 'era5_land') or (data_source == 'era5'):
                temp_df = pd.DataFrame(
                    {'bbox': str(bbox), 'time_start': time_start, 'time_end': time_end, 'res_lon': 0.1, 'res_lat': 0.1,
                     'path': tile_path}, index=default_index(1))
            else:
                temp_df = pd.DataFrame()
            metadata_df = pd.concat([metadata_df, temp_df], axis=0)
            if (data_source == 'gpm') or (data_source == 'smap') or (data_source == 'era5_land') or (
                data_source == 'era5'):
                hdscc.FS.write_bytes(tile_path, tile_ds.to_netcdf())
            elif data_source == 'gfs':
                tile_ds.to_netcdf('temp.nc4')
                hdscc.FS.put_file('temp.nc4', tile_path)
                os.remove('temp.nc4')
        metadata_df.to_csv(f'{data_source}_metadata.csv', index=False)
        hdscc.FS.put_file(f'{data_source}_metadata.csv', f's3://grids-origin/{data_source}_metadata.csv')
        os.remove(f'{data_source}_metadata.csv')
    return tile_list


def choose_gfs(paths, start_time, end_time):
    """
        This function chooses GFS data within a specified time range and bounding box.
        Args:
            paths (Dataframe): A list of GFS data paths.
            start_time (datetime, YY-mm-dd): The start time of the desired data.
            end_time (datetime, YY-mm-dd): The end time of the desired data.
        Returns:
            list: A list of GFS data within the specified time range and bounding box.
        """
    path_list = []
    produce_times = ['00', '06', '12', '18']
    if start_time is None:
        start_time = paths['time_start'].iloc[0]
    if end_time is None:
        end_time = paths['time_end'].iloc[-1]
    time_range = pd.date_range(start_time, end_time, freq='1D')
    for date in time_range:
        date_str = date.strftime('%Y/%m/%d')
        for i in range(len(produce_times)):
            for j in range(6 * i, 6 * (i + 1)):
                path = 's3://grids-origin/GFS/GEE/1h/' + date_str + '/' + produce_times[i] + '/gfs20220103.t' + \
                       produce_times[i] + 'z.nc4.0p25.f' + '{:03d}'.format(j)
                path_list.append(path)
    return path_list


def string_to_list(x: str):
    return list(map(float, x[1:-1].split(',')))


def generate_bbox_from_shp(basin_shape_path, data_source, minio=True):
    # 只考虑单个流域
    if minio:
        basin_gpd = access_fs.spec_path(basin_shape_path.lstrip('s3://'), head="minio")
    else:
        basin_gpd = gpd.read_file(basin_shape_path)
    if data_source == 'smap':
        # 西，东，北，南
        bounds_array = basin_gpd.bounds.to_numpy()[0]
        bbox = [bounds_array[0], bounds_array[2], bounds_array[3], bounds_array[1]]
    else:
        mask = hpm.gen_single_mask(basin_gpd, dataname=data_source)
        bbox = [mask['lon'].values.min(), mask['lon'].values.max(), mask['lat'].values.max(), mask['lat'].values.min()]
    return bbox, basin_gpd


def grid_mean_mask(basin_id, times: list, data_source):
    # basin_id: basin_CHN_songliao_21401550, 碧流河
    # times: [[2023-06-06 00:00:00, 2023-06-06 02:00:00], [2023-06-07 00:00:00, 2023-06-07 02:00:00]]
    basin_shp = f's3://basins-origin/basin_shapefiles/{basin_id}.zip'
    bbox, basin = generate_bbox_from_shp(basin_shp, data_source=data_source)
    aoi_data_paths = []
    for time_slice in times:
        time_start = time_slice[0]
        time_end = time_slice[1]
        aoi_path = query_path_from_metadata(basin_id, time_start, time_end, bbox, data_source=data_source)
        aoi_data_paths.append(aoi_path)
    result_arr_list = []
    for time_paths in aoi_data_paths:
        for path in time_paths:
            aoi_dataset = xr.open_dataset(hdscc.FS.open(path))
            mask = hpm.gen_single_mask(basin, data_source)
            if data_source == 'gpm':
                # 按照mask出来的四至和get_para()有关，全是.0或.5，直接输入四至就会破坏这样的性质
                result_arr = hpm.mean_by_mask(aoi_dataset, var='precipitationCal', mask=mask)
            elif (data_source == 'era5_land') | (data_source == 'era5'):
                result_arr = hpm.mean_by_mask(aoi_dataset, var='tp', mask=mask)
            elif data_source == 'smap':
                result_arr = hpm.mean_by_mask(aoi_dataset, var='sm_surface', mask=mask)
            elif data_source == 'gfs':
                # gfs降水字段不一定是lev_surface, 仅作演示
                result_arr = hpm.mean_by_mask(aoi_dataset, var='lev_surface', mask=mask)
            else:
                result_arr = []
            result_arr_list.append(result_arr)
    return result_arr_list, basin


def concat_gpm_average(basin_id, times: list):
    result_arr_list, basin = grid_mean_mask(basin_id, times, 'gpm')
    gpm_hour_array = []
    for i in np.arange(0, len(result_arr_list) - 1, 2):
        gpm_hour_i = np.add(result_arr_list[i], result_arr_list[i + 1])
        gpm_hour_array.append(gpm_hour_i)
    # temporarily fix
    if len(gpm_hour_array) % 2 != 0:
        gpm_hour_array.extend(gpm_hour_array[-1])
    return np.array(gpm_hour_array, dtype=object)


def concat_gpm_smap_mean_data(basin_ids: list, times: list):
    pp_sta_gdf = gpd.read_file(hdscc.FS.open('s3://stations-origin/stations_list/pp_stations.zip'))
    merge_list = []
    for basin_id in basin_ids:
        gpm_mean = concat_gpm_average(basin_id, times)
        smap_mean_mask, basin_gdf = grid_mean_mask(basin_id, times, 'smap')
        pp_stas_basin = gpd.overlay(pp_sta_gdf, basin_gdf, 'intersection')
        # 如果新站对不上老时间，就会把老数据也砍掉
        average_rainfall = rainfall_average(basin_gdf, pp_stas_basin, pp_stas_basin['ID'].to_list(), times[0][0])
        average_rainfall_times = average_rainfall[average_rainfall['TM'].isin(convert_time_slice_to_range(times))]
        smap_mean = np.repeat(smap_mean_mask, 3, axis=1).flatten()
        if smap_mean.shape[0] < gpm_mean.shape[0]:
            diff = gpm_mean.shape[0] - smap_mean.shape[0]
            smap_mean = np.pad(smap_mean, (0, diff), 'edge')
        else:
            smap_mean = smap_mean[:gpm_mean.shape[0]]
        streamflow_arr = read_streamflow_from_minio(times, basin_id.lstrip('basin_'))
        temp_df = pd.DataFrame({'time': convert_time_slice_to_range(times), 'gpm_tp': gpm_mean, 'smap': smap_mean,
                                'sta_tp': average_rainfall_times['weighted_rainfall'].to_numpy(),
                                'streamflow': streamflow_arr}).set_index(keys=['time'])
        merge_ds = xr.Dataset.from_dataframe(temp_df.astype('float64'))
        merge_list.append(merge_ds)
    return merge_list


def convert_time_slice_to_range(time_slice_list):
    time_range_list = []
    for time_slice in time_slice_list:
        pd_time_slice = pd.date_range(time_slice[0], time_slice[1], freq='H').to_list()
        time_range_list.extend(pd_time_slice)
    return time_range_list


def read_streamflow_from_minio(times: list, sta_id=''):
    # sta_id: CHN_songliao_21401550
    zq_path = f's3://stations-origin/zq_stations/hour_data/1h/zq_{sta_id}.csv'
    zz_1h_path = f's3://stations-origin/zz_stations/hour_data/1h/zz_{sta_id}.csv'
    zz_6h_path = f's3://stations-origin/zz_stations/hour_data/6h/zz_{sta_id}.csv'
    if hdscc.FS.exists(zq_path):
        streamflow_df = pd.read_csv(hdscc.FS.open(zq_path), index_col=None, parse_dates=['TM'])
    elif hdscc.FS.exists(zz_1h_path):
        streamflow_df = pd.read_csv(hdscc.FS.open(zz_1h_path), index_col=None, parse_dates=['TM'])
    elif hdscc.FS.exists(zz_6h_path):
        streamflow_df = pd.read_csv(hdscc.FS.open(zz_6h_path), index_col=None, parse_dates=['TM'])
    else:
        streamflow_df = pd.DataFrame
    streamflow_df = streamflow_df[streamflow_df['TM'].isin(convert_time_slice_to_range(times))]
    return streamflow_df['Q'].to_numpy()


'''
def merge_with_spatial_average(gpm_file, gfs_file, smap_file, output_file_path):
    def calculate_and_rename(input_file_path, prefix):
        ds = access_fs.spec_path(input_file_path, head="minio")
        avg_ds = ds.mean(dim=["lat", "lon"], skipna=True).astype("float32")
        new_names = {var_name: (
            f"{prefix}_tp" if var_name in ["tp", "__xarray_dataarray_variable__"] else f"{prefix}_{var_name}") for
            var_name in avg_ds.data_vars}
        avg_ds_renamed = avg_ds.rename(new_names)
        return avg_ds_renamed
    basin_id = output_file_path.split('_')[-1].split('.')[0]
    gfs_avg_renamed = calculate_and_rename(gfs_file, "gfs")
    gpm_avg_renamed = calculate_and_rename(gpm_file, "gpm")
    smap_avg_renamed = calculate_and_rename(smap_file, "smap")
    intersect_time = np.intersect1d(gfs_avg_renamed.time.values, gpm_avg_renamed.time.values, assume_unique=True)
    intersect_time = np.intersect1d(intersect_time, smap_avg_renamed.time.values, assume_unique=True)
    gfs_intersected = gfs_avg_renamed.sel(time=intersect_time)
    gpm_intersected = gpm_avg_renamed.sel(time=intersect_time)
    smap_intersected = smap_avg_renamed.sel(time=intersect_time)
    merged_ds = xr.merge([gfs_intersected, gpm_intersected, smap_intersected])
    merged_ds = merged_ds.assign_coords({"basin": basin_id}).expand_dims("basin")
    conf.FS.write_bytes(output_file_path, merged_ds.to_netcdf())
    return merged_ds
'''
