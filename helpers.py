import cv2
import numpy as np
from shapely.geometry import Polygon, Point
from shapely.affinity import scale
from itertools import product
from PIL import Image, ImageFont, ImageDraw, ImageChops
import streamlit as st

class Homography():
    def __init__(self, pts_src, pts_dst):
        self.pts_src = np.array(pts_src)
        self.pts_dst = np.array(pts_dst)
        self.h, out = cv2.findHomography(self.pts_src, self.pts_dst)
        self.im_size = (525, 340)
        self.im_width = self.im_size[0]
        self.im_heigth = self.im_size[1]
        self.coord_converter = np.array(self.im_size)/100

    def apply_to_image(self, image):
        im_out = cv2.warpPerspective(np.array(image.im), self.h, self.im_size)
        return im_out

    def apply_to_points(self, points, inverse=False):
        h = np.linalg.inv(self.h) if inverse else self.h
        _points = np.hstack([points, np.ones((len(points), 1))])
        _converted_points = np.dot(h,_points.T)
        points = _converted_points/_converted_points[2]
        return points[:2].T

class VoronoiPitch():
    def __init__(self, df):
        self.vor, self.df = calculate_voronoi(df)

    def get_regions(self):
        return [index for index, region in enumerate(self.vor.regions) if (not -1 in region) and (len(region)>0)]
    
    def get_points_region(self, region):
        return np.vstack([self.vor.vertices[i] for i in self.vor.regions[region]])
    
    def get_color_region(self, region):
        return self.df[self.df['region']==region]['team'].values[0]

    def get_voronoi_polygons(self, image, original=True):
        return [{'polygon': get_polygon(self.get_points_region(region)*image.h.coord_converter, image, original),
                 'color': self.get_color_region(region)}
                for region in self.get_regions()]

class PitchImage():
    def __init__(self, image_to_open, pitch, width=600):
        self.im = self.open(image_to_open, width=width)
        self.pitch = pitch

    def open(self, image_to_open, width):
        im = Image.open(image_to_open)
        im = im.resize((width, int(width*im.height/im.width)))
        return im
    
    def set_info(self, df, lines):
        df['line'] = lines
        df['y1_line'] = df['top']+df['y1']
        df['y2_line'] = df['top']+df['y2']
        df['x1_line'] = df['left']+df['x1']
        df['x2_line'] = df['left']+df['x2']
        df['slope'], df['intercept'] = get_si_from_coords(df[['x1_line', 'y1_line', 'x2_line', 'y2_line']].values)
        df = df.set_index('line')
        self.df = df
        self.lines = lines
        self.h = Homography(*self.get_intersections())
        self.conv_im = Image.fromarray(self.h.apply_to_image(self))

    def get_intersections(self):
        lines = self.lines
        vertical_lines = [x for x in lines if x in self.pitch.vert_lines]
        horizontal_lines = [x for x in lines if x in self.pitch.horiz_lines]
        intersections = {'_'.join([v, h]): line_intersect(self.df.loc[v, ['slope', 'intercept']], self.df.loc[h, ['slope', 'intercept']])
                            for v,h in product(vertical_lines, horizontal_lines)}

        pts_src = list(intersections.values())
        pts_dst = [self.pitch.get_intersections()[x] for x in intersections]

        return pts_src, pts_dst

    def get_image(self, original=True):
        return self.im if original else self.conv_im

    def get_pitch_coords(self):
        return ((0,0), (0,self.h.im_heigth), (self.h.im_width,self.h.im_heigth), (self.h.im_width,0))

    def get_camera_coords(self):
        return self.h.apply_to_points(((0,0), (0,self.im.height), (self.im.width, self.im.height), (self.im.width,0)))


class PitchDraw():
    def __init__(self, pitch_image, original=True):
        self.base_im = pitch_image.get_image(original).copy()
        self.draw_im = Image.new('RGBA', self.base_im.size, (0,0,0,0))
        self.draw = ImageDraw.Draw(self.draw_im, mode='RGBA')
        self.original = original
        self.h = pitch_image.h

    def draw_polygon(self, polygon, color):
        self.draw.polygon(list(tuple(point) for point in polygon.tolist()), fill=color, outline='gray')

    def draw_voronoi(self, voronoi, image, opacity):
        for pol in voronoi.get_voronoi_polygons(image, self.original):
            if pol['polygon'] is not None:
                if pol['color'] == 'red':
                    fill_color=(255,0,0,opacity)
                else:
                    fill_color=(0,0,255,opacity)
                self.draw_polygon(pol['polygon'], fill_color)

    def draw_circle(self, xy, color, size=1):
        center = Point(*xy)
        scaler = self.h.coord_converter/self.h.coord_converter.sum()*2
        circle = scale(center.buffer(size), *reversed(scaler))
        if self.original:
            points = self.h.apply_to_points(np.vstack(circle.exterior.xy).T*self.h.coord_converter, inverse=True)
        else:
            points = np.vstack(circle.exterior.xy).T*self.h.coord_converter
        self.draw_polygon(points, color)

    def draw_text(self, xy, string, color):
        xy = xy*self.h.coord_converter
        font = ImageFont.truetype("arial.ttf", size=12)
        if self.original:
            xy = self.h.apply_to_points([xy], inverse=True)[0]
        self.draw.text(tuple(xy), string, font=font, fill=color, stroke_fill='white', stroke_width=2)

    def compose_image(self, sensitivity=25):
        pitch_mask = get_edge_img(self.base_im, sensitivity=sensitivity)
        self.draw_im.putalpha(Image.fromarray(np.minimum(pitch_mask, np.array(self.draw_im.split()[-1]))))
        return Image.alpha_composite(self.base_im.convert("RGBA"), self.draw_im)


def line_intersect(si1, si2):
    m1, b1 = si1
    m2, b2 = si2
    if m1 == m2:
        return None
    x = (b2 - b1) / (m1 - m2)
    y = m1 * x + b1
    return x,y

def get_si_from_coords(lines):
    x1, y1, x2, y2 = lines.T
    slope = (y2-y1) / (x2-x1)
    intercept = y2-slope*x2
    return slope, intercept

def calculate_voronoi(df):
    from scipy.spatial import Voronoi
    values = np.vstack((df[['x', 'y']].values,
                        [-1000,-1000],
                        [+1000,+1000],
                        [+1000,-1000],
                        [-1000,+1000]
                       ))
    vor = Voronoi(values)
    df['region'] = vor.point_region[:-4]
    return vor, df

def get_polygon(points, image, convert):
    base_polygon = Polygon(points.tolist())
    pitch_polygon = Polygon(image.get_pitch_coords())
    camera_polygon = Polygon(image.get_camera_coords()).convex_hull
    polygon = camera_polygon.intersection(pitch_polygon).intersection(base_polygon)
    if polygon.area>0:
        if convert:
            polygon = image.h.apply_to_points(np.vstack(polygon.exterior.xy).T, inverse=True)
        else:
            polygon = np.vstack(polygon.exterior.xy).T
        return polygon
    else:
        return None
    
def get_edge_img(img, sensitivity=25):
    hsv_img = cv2.cvtColor(np.array(img), cv2.COLOR_BGR2HSV)
    hues = hsv_img[:,:,0]
    median_hue = np.median(hues[hues>1])
    min_filter = np.array([median_hue - sensitivity, 20, 0])
    max_filter = np.array([median_hue + sensitivity, 255, 255])

    mask = cv2.inRange(hsv_img, min_filter, max_filter)
    return mask
