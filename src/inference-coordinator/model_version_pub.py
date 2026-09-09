#!/usr/bin/env python3
# This project was developed with assistance from AI tools.
"""Latched /flywheel/model_version publisher for the policy role, where no coordinator runs."""

import os
import sys

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile
from std_msgs.msg import String

# Same QoS as the coordinator's publisher and the emitter's subscriber: late joiners still get the label.
LATCHED = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, history=HistoryPolicy.KEEP_LAST)


def main():
    mv = os.environ.get("MODEL_VERSION", "").strip()
    if not mv:
        print("[model-version] MODEL_VERSION is unset; nothing to publish", file=sys.stderr, flush=True)
        sys.exit(2)
    rclpy.init()
    node = Node("model_version_publisher")
    pub = node.create_publisher(String, "/flywheel/model_version", LATCHED)
    pub.publish(String(data=mv))
    node.get_logger().info(f"Published model_version: {mv}")
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
