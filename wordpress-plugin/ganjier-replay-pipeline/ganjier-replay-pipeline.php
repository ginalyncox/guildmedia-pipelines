<?php
/**
 * Plugin Name: Ganjier Replay Pipeline
 * Description: Admin dashboard and REST API for the Zoom replay automation pipeline. Logs pipeline runs and proxies Zoom webhooks to pipeline.py.
 * Version: 1.0.0
 * Author: Ganjier Guild
 * Requires at least: 6.0
 * Requires PHP: 7.4
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'GG_PIPELINE_VERSION', '1.0.0' );
define( 'GG_PIPELINE_TABLE', 'gg_pipeline_runs' );

register_activation_hook( __FILE__, 'gg_pipeline_activate' );

/**
 * Create the pipeline runs table on plugin activation.
 */
function gg_pipeline_activate() {
	global $wpdb;

	$table_name      = $wpdb->prefix . GG_PIPELINE_TABLE;
	$charset_collate = $wpdb->get_charset_collate();

	$sql = "CREATE TABLE {$table_name} (
		id bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
		recording_id varchar(255) NOT NULL DEFAULT '',
		topic text NOT NULL,
		recording_date date DEFAULT NULL,
		duration_min smallint(5) UNSIGNED NOT NULL DEFAULT 0,
		zoom_account varchar(64) NOT NULL DEFAULT '',
		youtube_url varchar(512) NOT NULL DEFAULT '',
		wp_url varchar(512) NOT NULL DEFAULT '',
		status varchar(64) NOT NULL DEFAULT 'pending',
		error text NULL,
		processed_at datetime DEFAULT NULL,
		created_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
		updated_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
		PRIMARY KEY  (id),
		KEY recording_id (recording_id),
		KEY status (status),
		KEY processed_at (processed_at)
	) {$charset_collate};";

	require_once ABSPATH . 'wp-admin/includes/upgrade.php';
	dbDelta( $sql );
}

/**
 * Return the fully-qualified pipeline runs table name.
 */
function gg_pipeline_table_name() {
	global $wpdb;
	return $wpdb->prefix . GG_PIPELINE_TABLE;
}

add_action( 'rest_api_init', 'gg_pipeline_register_rest_routes' );

/**
 * Register REST routes for pipeline logging and webhook proxying.
 */
function gg_pipeline_register_rest_routes() {
	register_rest_route(
		'gg/v1',
		'/pipeline-runs',
		[
			[
				'methods'             => WP_REST_Server::CREATABLE,
				'callback'            => 'gg_pipeline_upsert_run',
				'permission_callback' => 'gg_pipeline_can_log_runs',
				'args'                => gg_pipeline_run_args(),
			],
			[
				'methods'             => WP_REST_Server::READABLE,
				'callback'            => 'gg_pipeline_list_runs',
				'permission_callback' => 'gg_pipeline_can_view_dashboard',
				'args'                => [
					'per_page' => [
						'type'              => 'integer',
						'default'           => 50,
						'minimum'           => 1,
						'maximum'           => 200,
						'sanitize_callback' => 'absint',
					],
					'status'   => [
						'type'              => 'string',
						'default'           => '',
						'sanitize_callback' => 'sanitize_text_field',
					],
				],
			],
		]
	);

	register_rest_route(
		'gg/v1',
		'/pipeline-runs/probe',
		[
			'methods'             => WP_REST_Server::READABLE,
			'callback'            => 'gg_pipeline_probe_logging',
			'permission_callback' => 'gg_pipeline_can_log_runs',
		]
	);

	register_rest_route(
		'gg/v1',
		'/zoom-webhook',
		[
			'methods'             => [ 'GET', 'POST' ],
			'callback'            => 'gg_zoom_webhook_proxy',
			'permission_callback' => '__return_true',
		]
	);
}

/**
 * REST argument schema for pipeline run logging.
 */
function gg_pipeline_run_args() {
	return [
		'recording_id'   => [ 'type' => 'string', 'required' => false ],
		'topic'          => [ 'type' => 'string', 'required' => true ],
		'recording_date' => [ 'type' => 'string', 'required' => false ],
		'duration_min'   => [ 'type' => 'integer', 'required' => false ],
		'zoom_account'   => [ 'type' => 'string', 'required' => false ],
		'youtube_url'    => [ 'type' => 'string', 'required' => false ],
		'wp_url'         => [ 'type' => 'string', 'required' => false ],
		'status'         => [ 'type' => 'string', 'required' => true ],
		'error'          => [ 'type' => 'string', 'required' => false ],
		'processed_at'   => [ 'type' => 'string', 'required' => false ],
	];
}

/**
 * Allow logging when the request is authenticated and the user can edit posts.
 */
function gg_pipeline_can_log_runs() {
	return current_user_can( 'edit_posts' );
}

/**
 * Dashboard data is visible to site administrators.
 */
function gg_pipeline_can_view_dashboard() {
	return current_user_can( 'manage_options' );
}

/**
 * Verify credentials can log pipeline runs without writing a row.
 */
function gg_pipeline_probe_logging() {
	return new WP_REST_Response(
		[
			'ok' => true,
		],
		200
	);
}

/**
 * Insert or update a pipeline run row keyed by recording_id.
 */
function gg_pipeline_upsert_run( WP_REST_Request $request ) {
	global $wpdb;

	$table = gg_pipeline_table_name();
	$data  = [
		'recording_id'   => sanitize_text_field( (string) $request->get_param( 'recording_id' ) ),
		'topic'          => sanitize_text_field( (string) $request->get_param( 'topic' ) ),
		'recording_date' => gg_pipeline_parse_date( $request->get_param( 'recording_date' ) ),
		'duration_min'   => absint( $request->get_param( 'duration_min' ) ),
		'zoom_account'   => sanitize_text_field( (string) $request->get_param( 'zoom_account' ) ),
		'youtube_url'    => esc_url_raw( (string) $request->get_param( 'youtube_url' ) ),
		'wp_url'         => esc_url_raw( (string) $request->get_param( 'wp_url' ) ),
		'status'         => sanitize_text_field( (string) $request->get_param( 'status' ) ),
		'error'          => sanitize_textarea_field( (string) $request->get_param( 'error' ) ),
		'processed_at'   => gg_pipeline_parse_datetime( $request->get_param( 'processed_at' ) ),
	];

	if ( empty( $data['topic'] ) || empty( $data['status'] ) ) {
		return new WP_Error( 'gg_pipeline_invalid', 'topic and status are required.', [ 'status' => 400 ] );
	}

	$existing_id = 0;
	if ( ! empty( $data['recording_id'] ) ) {
		$existing_id = (int) $wpdb->get_var(
			$wpdb->prepare(
				"SELECT id FROM {$table} WHERE recording_id = %s ORDER BY id DESC LIMIT 1",
				$data['recording_id']
			)
		);
	}

	if ( $existing_id ) {
		$wpdb->update( $table, $data, [ 'id' => $existing_id ] );
		$row_id = $existing_id;
	} else {
		$wpdb->insert( $table, $data );
		$row_id = (int) $wpdb->insert_id;
	}

	return new WP_REST_Response(
		[
			'id'      => $row_id,
			'updated' => (bool) $existing_id,
			'run'     => gg_pipeline_format_run( array_merge( [ 'id' => $row_id ], $data ) ),
		],
		$existing_id ? 200 : 201
	);
}

/**
 * List recent pipeline runs for the admin dashboard / API consumers.
 */
function gg_pipeline_list_runs( WP_REST_Request $request ) {
	global $wpdb;

	$table   = gg_pipeline_table_name();
	$per_page = min( 200, max( 1, absint( $request->get_param( 'per_page' ) ) ) );
	$status   = sanitize_text_field( (string) $request->get_param( 'status' ) );

	$sql    = "SELECT * FROM {$table}";
	$params = [];

	if ( $status ) {
		$sql     .= ' WHERE status = %s';
		$params[] = $status;
	}

	$sql     .= ' ORDER BY processed_at DESC, id DESC LIMIT %d';
	$params[] = $per_page;

	$prepared = $params ? $wpdb->prepare( $sql, $params ) : $wpdb->prepare( $sql, $per_page );
	$rows     = $wpdb->get_results( $prepared, ARRAY_A );

	return new WP_REST_Response(
		[
			'runs'  => array_map( 'gg_pipeline_format_run', $rows ?: [] ),
			'count' => count( $rows ?: [] ),
		],
		200
	);
}

/**
 * Normalize a DB row for API / dashboard output.
 */
function gg_pipeline_format_run( $row ) {
	return [
		'id'             => isset( $row['id'] ) ? (int) $row['id'] : 0,
		'recording_id'   => $row['recording_id'] ?? '',
		'topic'          => $row['topic'] ?? '',
		'recording_date' => $row['recording_date'] ?? '',
		'duration_min'   => isset( $row['duration_min'] ) ? (int) $row['duration_min'] : 0,
		'zoom_account'   => $row['zoom_account'] ?? '',
		'youtube_url'    => $row['youtube_url'] ?? '',
		'wp_url'         => $row['wp_url'] ?? '',
		'status'         => $row['status'] ?? '',
		'error'          => $row['error'] ?? '',
		'processed_at'   => $row['processed_at'] ?? '',
		'created_at'     => $row['created_at'] ?? '',
		'updated_at'     => $row['updated_at'] ?? '',
	];
}

/**
 * Parse YYYY-MM-DD dates from pipeline payloads.
 */
function gg_pipeline_parse_date( $value ) {
	$value = sanitize_text_field( (string) $value );
	if ( ! $value ) {
		return null;
	}
	$timestamp = strtotime( $value );
	return $timestamp ? gmdate( 'Y-m-d', $timestamp ) : null;
}

/**
 * Parse ISO-like datetimes from pipeline payloads.
 */
function gg_pipeline_parse_datetime( $value ) {
	$value = sanitize_text_field( (string) $value );
	if ( ! $value ) {
		return current_time( 'mysql', true );
	}
	$timestamp = strtotime( $value );
	return $timestamp ? gmdate( 'Y-m-d H:i:s', $timestamp ) : current_time( 'mysql', true );
}

/**
 * Proxy Zoom webhook traffic to the local pipeline process.
 */
function gg_zoom_webhook_proxy( WP_REST_Request $request ) {
	$pipeline_url = apply_filters( 'gg_pipeline_webhook_url', 'http://127.0.0.1:5055/zoom/webhook' );
	$body         = $request->get_body();
	$headers      = $request->get_headers();
	$forward      = [ 'Content-Type' => 'application/json' ];

	foreach ( [ 'x_zm_signature', 'x_zm_request_timestamp' ] as $key ) {
		if ( ! empty( $headers[ $key ][0] ) ) {
			$forward[ str_replace( '_', '-', $key ) ] = $headers[ $key ][0];
		}
	}

	$response = wp_remote_post(
		$pipeline_url,
		[
			'body'      => $body,
			'headers'   => $forward,
			'timeout'   => 30,
			'sslverify' => false,
		]
	);

	if ( is_wp_error( $response ) ) {
		error_log( '[Ganjier Replay Pipeline] Pipeline unreachable: ' . $response->get_error_message() );
		return new WP_REST_Response( [ 'error' => 'pipeline unavailable' ], 503 );
	}

	$pipeline_body = wp_remote_retrieve_body( $response );
	$pipeline_code = wp_remote_retrieve_response_code( $response );

	return new WP_REST_Response(
		json_decode( $pipeline_body, true ) ?: $pipeline_body,
		$pipeline_code ?: 200
	);
}

add_action( 'admin_menu', 'gg_pipeline_register_admin_menu' );
add_action( 'admin_enqueue_scripts', 'gg_pipeline_admin_assets' );

/**
 * Add the Replay Pipeline dashboard under Tools.
 */
function gg_pipeline_register_admin_menu() {
	add_management_page(
		'Replay Pipeline',
		'Replay Pipeline',
		'manage_options',
		'gg-replay-pipeline',
		'gg_pipeline_render_dashboard'
	);
}

/**
 * Load minimal admin styles on the dashboard page.
 */
function gg_pipeline_admin_assets( $hook ) {
	if ( 'tools_page_gg-replay-pipeline' !== $hook ) {
		return;
	}

	wp_register_style( 'gg-pipeline-admin', false );
	wp_enqueue_style( 'gg-pipeline-admin' );
	wp_add_inline_style(
		'gg-pipeline-admin',
		'.gg-pipeline-wrap .status-completed{color:#1d7a46;font-weight:600;}
		 .gg-pipeline-wrap .status-failed{color:#b32d2e;font-weight:600;}
		 .gg-pipeline-wrap table.widefat td{vertical-align:top;}
		 .gg-pipeline-wrap .gg-pipeline-summary{display:flex;gap:16px;margin:16px 0;}
		 .gg-pipeline-wrap .gg-pipeline-card{background:#fff;border:1px solid #dcdcde;border-radius:4px;padding:12px 16px;min-width:140px;}'
	);
}

/**
 * Render the admin dashboard table.
 */
function gg_pipeline_render_dashboard() {
	if ( ! current_user_can( 'manage_options' ) ) {
		return;
	}

	global $wpdb;
	$table = gg_pipeline_table_name();
	$rows  = $wpdb->get_results(
		$wpdb->prepare(
			"SELECT * FROM {$table} ORDER BY processed_at DESC, id DESC LIMIT %d",
			100
		),
		ARRAY_A
	);

	$completed = 0;
	$failed    = 0;
	foreach ( $rows as $row ) {
		if ( 0 === strpos( (string) $row['status'], 'failed' ) ) {
			$failed++;
		} elseif ( 'completed' === $row['status'] ) {
			$completed++;
		}
	}

	?>
	<div class="wrap gg-pipeline-wrap">
		<h1>Replay Pipeline</h1>
		<p>Automation status for Zoom → YouTube → WordPress replay processing.</p>

		<div class="gg-pipeline-summary">
			<div class="gg-pipeline-card"><strong><?php echo esc_html( count( $rows ) ); ?></strong><br>Recent runs</div>
			<div class="gg-pipeline-card"><strong><?php echo esc_html( $completed ); ?></strong><br>Completed</div>
			<div class="gg-pipeline-card"><strong><?php echo esc_html( $failed ); ?></strong><br>Failed</div>
		</div>

		<p>
			Pipeline logs via
			<code>POST <?php echo esc_html( rest_url( 'gg/v1/pipeline-runs' ) ); ?></code>
			using the same WordPress Application Password as the Python pipeline.
		</p>

		<table class="widefat striped">
			<thead>
				<tr>
					<th>Topic</th>
					<th>Date</th>
					<th>Account</th>
					<th>Status</th>
					<th>YouTube</th>
					<th>Replay</th>
					<th>Processed</th>
					<th>Error</th>
				</tr>
			</thead>
			<tbody>
			<?php if ( empty( $rows ) ) : ?>
				<tr><td colspan="8">No pipeline runs logged yet.</td></tr>
			<?php else : ?>
				<?php foreach ( $rows as $row ) : ?>
					<tr>
						<td><?php echo esc_html( $row['topic'] ); ?></td>
						<td><?php echo esc_html( $row['recording_date'] ); ?></td>
						<td><?php echo esc_html( $row['zoom_account'] ); ?></td>
						<td class="<?php echo esc_attr( gg_pipeline_status_class( $row['status'] ) ); ?>">
							<?php echo esc_html( $row['status'] ); ?>
						</td>
						<td>
							<?php if ( ! empty( $row['youtube_url'] ) ) : ?>
								<a href="<?php echo esc_url( $row['youtube_url'] ); ?>" target="_blank" rel="noopener noreferrer">YouTube</a>
							<?php endif; ?>
						</td>
						<td>
							<?php if ( ! empty( $row['wp_url'] ) ) : ?>
								<a href="<?php echo esc_url( $row['wp_url'] ); ?>" target="_blank" rel="noopener noreferrer">Replay</a>
							<?php endif; ?>
						</td>
						<td><?php echo esc_html( $row['processed_at'] ); ?></td>
						<td><?php echo esc_html( $row['error'] ); ?></td>
					</tr>
				<?php endforeach; ?>
			<?php endif; ?>
			</tbody>
		</table>
	</div>
	<?php
}

/**
 * CSS helper for status cells.
 */
function gg_pipeline_status_class( $status ) {
	if ( 'completed' === $status ) {
		return 'status-completed';
	}
	if ( 0 === strpos( (string) $status, 'failed' ) ) {
		return 'status-failed';
	}
	return '';
}
