<?php
/**
 * Plugin Name: Zoom Webhook Bridge
 * Description: Proxies Zoom webhook events from WordPress REST API to the local pipeline.py process on port 5055.
 * Version: 1.0.0
 * Author: Ganjier Guild
 */

if ( ! defined( 'ABSPATH' ) ) exit;

add_action( 'rest_api_init', function () {
    register_rest_route( 'gg/v1', '/zoom-webhook', [
        'methods'             => [ 'GET', 'POST' ],
        'callback'            => 'gg_zoom_webhook_proxy',
        'permission_callback' => '__return_true',
    ] );
} );

function gg_zoom_webhook_proxy( WP_REST_Request $request ) {
    $pipeline_url = 'http://127.0.0.1:5055/zoom/webhook';

    $body    = $request->get_body();
    $headers = $request->get_headers();

    // Forward relevant Zoom signature headers
    $forward_headers = [];
    foreach ( [ 'x_zm_signature', 'x_zm_request_timestamp' ] as $key ) {
        if ( ! empty( $headers[ $key ][0] ) ) {
            $header_name = str_replace( '_', '-', $key );
            $forward_headers[ $header_name ] = $headers[ $key ][0];
        }
    }
    $forward_headers['Content-Type'] = 'application/json';

    // Forward to pipeline.py
    $response = wp_remote_post( $pipeline_url, [
        'body'        => $body,
        'headers'     => $forward_headers,
        'timeout'     => 30,
        'sslverify'   => false,
    ] );

    if ( is_wp_error( $response ) ) {
        error_log( '[Zoom Webhook Bridge] Pipeline unreachable: ' . $response->get_error_message() );
        return new WP_REST_Response( [ 'error' => 'pipeline unavailable' ], 503 );
    }

    $pipeline_body = wp_remote_retrieve_body( $response );
    $pipeline_code = wp_remote_retrieve_response_code( $response );

    // Pass pipeline response back to Zoom (needed for endpoint.url_validation)
    return new WP_REST_Response(
        json_decode( $pipeline_body, true ) ?: $pipeline_body,
        $pipeline_code ?: 200
    );
}
