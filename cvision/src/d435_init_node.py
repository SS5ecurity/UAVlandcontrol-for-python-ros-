#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge, CvBridgeError
import cv2

class D435InitNode(Node):
    def __init__(self):
        super().__init__('d435_init_node')
        self.bridge = CvBridge()

        # 订阅RGB和深度图像话题
        self.rgb_sub_ = self.create_subscription(
            Image,
            '/camera/camera/color/image_raw',
            self.rgb_callback,
            1)

        self.depth_sub_ = self.create_subscription(
            Image,
            '/camera/camera/aligned_depth_to_color/image_raw',
            self.depth_callback,
            1)

        # 发布处理后的RGB和深度图像
        self.rgb_pub_ = self.create_publisher(
            Image,
            '/d435/rgb/image_raw',
            1)

        self.depth_pub_ = self.create_publisher(
            Image,
            '/d435/camera/depth/image_raw',
            1)

    def rgb_callback(self, msg):
        try:
            # 将ROS图像消息转换为OpenCV格式
            cv_ptr = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
            resized_img = None

            # 调整大小为640×480
            # cv2.resize(cv_ptr, resized_img, (640, 480))

            # 创建新的图像消息
            resized_msg = self.bridge.cv2_to_imgmsg(cv_ptr, encoding='bgr8')
            resized_msg.header = msg.header

            # 发布调整大小后的图像
            self.rgb_pub_.publish(resized_msg)
        except Exception as e:
            self.get_logger().error('cv_bridge exception: ' + str(e))

    def depth_callback(self, msg):
        try:
            # 将ROS图像消息转换为OpenCV格式
            cv_ptr = self.bridge.imgmsg_to_cv2(msg, desired_encoding='16UC1')
            resized_img = None

            # 调整大小为640×480
            # cv2.resize(cv_ptr, resized_img, (640, 480))

            # 创建新的图像消息
            resized_msg = self.bridge.cv2_to_imgmsg(cv_ptr, encoding='16UC1')
            resized_msg.header = msg.header

            # 发布调整大小后的图像
            self.depth_pub_.publish(resized_msg)
        except Exception as e:
            self.get_logger().error('cv_bridge exception: ' + str(e))

def main(args=None):
    rclpy.init(args=args)

    d435_init_node = D435InitNode()

    rclpy.spin(d435_init_node)

    d435_init_node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
