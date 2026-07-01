#ifndef WIFI_H
#define WIFI_H

#include <stdbool.h>

#include <zephyr/kernel.h>
#include <zephyr/net/net_if.h>
#include <zephyr/net/wifi_mgmt.h>

extern struct wifi_connect_req_params wifi_params;

int wifi_init(struct device *unused);
int wait_for_wifi_connection(void);
int wait_for_ipv4_address(void);
int connect_to_wifi(void);
void wifi_disconnect(void);
bool wifi_is_connected(void);
void wifi_set_event_logging(bool enabled);

#endif /* WIFI_H */
