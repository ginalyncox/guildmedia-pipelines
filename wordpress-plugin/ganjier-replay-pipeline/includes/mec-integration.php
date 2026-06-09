<?php
/**
 * Modern Events Calendar (MEC) integration for replay linking.
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'GG_MEC_REPLAY_META', 'gg_replay_url' );
define( 'GG_MEC_YOUTUBE_META', 'gg_youtube_url' );
define( 'GG_MEC_REPLAY_POST_META', 'gg_replay_post_id' );
define( 'GG_MEC_LINKED_AT_META', 'gg_replay_linked_at' );

add_action( 'rest_api_init', 'gg_mec_register_rest_routes' );
add_filter( 'the_content', 'gg_mec_append_replay_notice', 20 );

/**
 * Register MEC match/link REST routes.
 */
function gg_mec_register_rest_routes() {
	register_rest_route(
		'gg/v1',
		'/mec-events/match',
		[
			'methods'             => WP_REST_Server::READABLE,
			'callback'            => 'gg_mec_match_event_route',
			'permission_callback' => 'gg_pipeline_can_log_runs',
			'args'                => [
				'topic' => [
					'type'              => 'string',
					'required'          => true,
					'sanitize_callback' => 'sanitize_text_field',
				],
				'start' => [
					'type'              => 'string',
					'required'          => true,
					'sanitize_callback' => 'sanitize_text_field',
				],
				'min_score' => [
					'type'              => 'integer',
					'default'           => 40,
					'sanitize_callback' => 'absint',
				],
			],
		]
	);

	register_rest_route(
		'gg/v1',
		'/mec-events/(?P<id>\d+)/link-replay',
		[
			'methods'             => WP_REST_Server::CREATABLE,
			'callback'            => 'gg_mec_link_replay_route',
			'permission_callback' => 'gg_pipeline_can_log_runs',
			'args'                => [
				'id' => [
					'type'              => 'integer',
					'required'          => true,
					'sanitize_callback' => 'absint',
				],
				'replay_url' => [
					'type'              => 'string',
					'required'          => true,
					'sanitize_callback' => 'esc_url_raw',
				],
				'youtube_url' => [
					'type'              => 'string',
					'required'          => false,
					'sanitize_callback' => 'esc_url_raw',
				],
				'replay_post_id' => [
					'type'              => 'integer',
					'required'          => false,
					'sanitize_callback' => 'absint',
				],
				'occurrence_date' => [
					'type'              => 'string',
					'required'          => false,
					'sanitize_callback' => 'sanitize_text_field',
				],
			],
		]
	);
}

/**
 * Normalize strings for fuzzy title comparison.
 */
function gg_mec_normalize_title( $value ) {
	$value = strtolower( wp_strip_all_tags( (string) $value ) );
	$value = preg_replace( '/[^a-z0-9]+/', ' ', $value );
	return trim( preg_replace( '/\s+/', ' ', $value ) );
}

/**
 * Score how well a Zoom topic matches an MEC event title.
 */
function gg_mec_title_score( $topic, $event_title ) {
	$topic_norm = gg_mec_normalize_title( $topic );
	$title_norm = gg_mec_normalize_title( $event_title );

	if ( '' === $topic_norm || '' === $title_norm ) {
		return 0;
	}

	if ( false !== strpos( $title_norm, $topic_norm ) || false !== strpos( $topic_norm, $title_norm ) ) {
		return 100;
	}

	similar_text( $topic_norm, $title_norm, $percent );
	return (int) round( $percent );
}

/**
 * Return candidate MEC events occurring on a given local date.
 */
function gg_mec_candidates_for_date( $date_ymd ) {
	global $wpdb;

	$dates_table = $wpdb->prefix . 'mec_dates';
	$posts_table = $wpdb->posts;

	// phpcs:ignore WordPress.DB.DirectDatabaseQuery.DirectQuery, WordPress.DB.DirectDatabaseQuery.NoCaching
	if ( $wpdb->get_var( $wpdb->prepare( 'SHOW TABLES LIKE %s', $dates_table ) ) !== $dates_table ) {
		return [];
	}

	$sql = "
		SELECT p.ID, p.post_title, p.post_name, d.dstart, d.tstart, d.dend, d.tend
		FROM {$posts_table} p
		INNER JOIN {$dates_table} d ON d.post_id = p.ID
		WHERE p.post_type = 'mec-events'
		AND p.post_status = 'publish'
		AND d.dstart = %s
		ORDER BY d.tstart ASC
	";

	// phpcs:ignore WordPress.DB.PreparedSQL.NotPrepared, WordPress.DB.DirectDatabaseQuery.DirectQuery, WordPress.DB.DirectDatabaseQuery.NoCaching
	return $wpdb->get_results( $wpdb->prepare( $sql, $date_ymd ), ARRAY_A ) ?: [];
}

/**
 * Find the best matching MEC event for a Zoom recording.
 */
function gg_mec_match_event( $topic, $start_iso, $min_score = 40 ) {
	$timestamp = strtotime( $start_iso );
	if ( ! $timestamp ) {
		return null;
	}

	$timezone = wp_timezone();
	$base_dt  = ( new DateTimeImmutable( '@' . $timestamp ) )->setTimezone( $timezone );
	$dates    = [
		$base_dt->format( 'Y-m-d' ),
		$base_dt->modify( '-1 day' )->format( 'Y-m-d' ),
		$base_dt->modify( '+1 day' )->format( 'Y-m-d' ),
	];

	$best = null;
	foreach ( array_unique( $dates ) as $date_ymd ) {
		foreach ( gg_mec_candidates_for_date( $date_ymd ) as $row ) {
			$score = gg_mec_title_score( $topic, $row['post_title'] );
			if ( $score < $min_score ) {
				continue;
			}

			if ( null === $best || $score > $best['score'] ) {
				$best = [
					'event_id'        => (int) $row['ID'],
					'title'           => $row['post_title'],
					'url'             => get_permalink( (int) $row['ID'] ),
					'occurrence_date' => $row['dstart'],
					'score'           => $score,
				];
			}
		}
	}

	return $best;
}

/**
 * REST handler: match a Zoom recording to an MEC calendar event.
 */
function gg_mec_match_event_route( WP_REST_Request $request ) {
	$match = gg_mec_match_event(
		$request->get_param( 'topic' ),
		$request->get_param( 'start' ),
		absint( $request->get_param( 'min_score' ) )
	);

	if ( ! $match ) {
		return new WP_REST_Response( [ 'matched' => false ], 200 );
	}

	return new WP_REST_Response(
		[
			'matched' => true,
			'event'   => $match,
		],
		200
	);
}

/**
 * Attach replay metadata to an MEC event.
 */
function gg_mec_link_replay_to_event( $event_id, $replay_url, $youtube_url = '', $replay_post_id = 0, $occurrence_date = '' ) {
	$post = get_post( $event_id );
	if ( ! $post || 'mec-events' !== $post->post_type ) {
		return new WP_Error( 'gg_mec_invalid_event', 'MEC event not found.', [ 'status' => 404 ] );
	}

	update_post_meta( $event_id, GG_MEC_REPLAY_META, esc_url_raw( $replay_url ) );
	update_post_meta( $event_id, GG_MEC_YOUTUBE_META, esc_url_raw( $youtube_url ) );
	update_post_meta( $event_id, GG_MEC_REPLAY_POST_META, absint( $replay_post_id ) );
	update_post_meta( $event_id, GG_MEC_LINKED_AT_META, current_time( 'mysql', true ) );

	if ( $occurrence_date ) {
		update_post_meta( $event_id, 'gg_replay_occurrence_date', sanitize_text_field( $occurrence_date ) );
	}

	return [
		'event_id'   => $event_id,
		'event_url'  => get_permalink( $event_id ),
		'replay_url' => esc_url_raw( $replay_url ),
	];
}

/**
 * REST handler: link a replay to an MEC event.
 */
function gg_mec_link_replay_route( WP_REST_Request $request ) {
	$result = gg_mec_link_replay_to_event(
		absint( $request['id'] ),
		$request->get_param( 'replay_url' ),
		$request->get_param( 'youtube_url' ),
		absint( $request->get_param( 'replay_post_id' ) ),
		$request->get_param( 'occurrence_date' )
	);

	if ( is_wp_error( $result ) ) {
		return $result;
	}

	return new WP_REST_Response( $result, 200 );
}

/**
 * Show a replay call-to-action on linked MEC event pages.
 */
function gg_mec_append_replay_notice( $content ) {
	if ( ! is_singular( 'mec-events' ) || ! in_the_loop() || ! is_main_query() ) {
		return $content;
	}

	$event_id   = get_the_ID();
	$replay_url = get_post_meta( $event_id, GG_MEC_REPLAY_META, true );
	if ( ! $replay_url ) {
		return $content;
	}

	$youtube_url = get_post_meta( $event_id, GG_MEC_YOUTUBE_META, true );
	$linked_at   = get_post_meta( $event_id, GG_MEC_LINKED_AT_META, true );

	$notice  = '<div class="gg-mec-replay-notice" style="margin:1.5rem 0;padding:1rem 1.25rem;border-left:4px solid #1d7a46;background:#f3faf5;">';
	$notice .= '<strong>Replay available</strong><br />';
	$notice .= '<a href="' . esc_url( $replay_url ) . '">Watch this session in the Replay Library</a>';
	if ( $youtube_url ) {
		$notice .= ' &nbsp;|&nbsp; <a href="' . esc_url( $youtube_url ) . '">YouTube</a>';
	}
	if ( $linked_at ) {
		$notice .= '<br /><small>Linked ' . esc_html( $linked_at ) . ' UTC</small>';
	}
	$notice .= '</div>';

	return $notice . $content;
}
