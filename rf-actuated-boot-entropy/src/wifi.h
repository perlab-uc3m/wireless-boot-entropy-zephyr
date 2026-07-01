#ifndef AEB_WIFI_H
#define AEB_WIFI_H

#include <stdbool.h>
#include <zephyr/device.h>
#include <zephyr/net/net_if.h>
#include <zephyr/net/wifi_mgmt.h>

int wifi_init(struct device *unused);
int connect_to_wifi(void);
int wifi_wait_for_ipv4(int timeout_ms);
void wifi_disconnect(void);
bool wifi_is_connected(void);
void wifi_set_event_logging(bool enabled);
void wifi_print_ipv4(void);

#endif /* AEB_WIFI_H */
