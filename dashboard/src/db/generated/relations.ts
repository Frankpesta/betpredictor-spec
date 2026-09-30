import { relations } from "drizzle-orm/relations";
import { leagues, modelRuns, teams, teamAliases, matches, historicalOdds, oddsSnapshots, predictions, valueLegs, slipLegs, slips } from "./schema";

export const modelRunsRelations = relations(modelRuns, ({one, many}) => ({
	league: one(leagues, {
		fields: [modelRuns.leagueId],
		references: [leagues.id]
	}),
	predictions: many(predictions),
}));

export const leaguesRelations = relations(leagues, ({many}) => ({
	modelRuns: many(modelRuns),
	teams: many(teams),
	matches: many(matches),
}));

export const teamsRelations = relations(teams, ({one, many}) => ({
	league: one(leagues, {
		fields: [teams.leagueId],
		references: [leagues.id]
	}),
	teamAliases: many(teamAliases),
	matches_awayTeamId: many(matches, {
		relationName: "matches_awayTeamId_teams_id"
	}),
	matches_homeTeamId: many(matches, {
		relationName: "matches_homeTeamId_teams_id"
	}),
}));

export const teamAliasesRelations = relations(teamAliases, ({one}) => ({
	team: one(teams, {
		fields: [teamAliases.teamId],
		references: [teams.id]
	}),
}));

export const historicalOddsRelations = relations(historicalOdds, ({one}) => ({
	match: one(matches, {
		fields: [historicalOdds.matchId],
		references: [matches.id]
	}),
}));

export const matchesRelations = relations(matches, ({one, many}) => ({
	historicalOdds: many(historicalOdds),
	oddsSnapshots: many(oddsSnapshots),
	predictions: many(predictions),
	league: one(leagues, {
		fields: [matches.leagueId],
		references: [leagues.id]
	}),
	team_awayTeamId: one(teams, {
		fields: [matches.awayTeamId],
		references: [teams.id],
		relationName: "matches_awayTeamId_teams_id"
	}),
	team_homeTeamId: one(teams, {
		fields: [matches.homeTeamId],
		references: [teams.id],
		relationName: "matches_homeTeamId_teams_id"
	}),
	valueLegs: many(valueLegs),
}));

export const oddsSnapshotsRelations = relations(oddsSnapshots, ({one, many}) => ({
	match: one(matches, {
		fields: [oddsSnapshots.matchId],
		references: [matches.id]
	}),
	valueLegs: many(valueLegs),
}));

export const predictionsRelations = relations(predictions, ({one, many}) => ({
	modelRun: one(modelRuns, {
		fields: [predictions.modelRunId],
		references: [modelRuns.id]
	}),
	match: one(matches, {
		fields: [predictions.matchId],
		references: [matches.id]
	}),
	valueLegs: many(valueLegs),
}));

export const slipLegsRelations = relations(slipLegs, ({one}) => ({
	valueLeg: one(valueLegs, {
		fields: [slipLegs.valueLegId],
		references: [valueLegs.id]
	}),
	slip: one(slips, {
		fields: [slipLegs.slipId],
		references: [slips.id]
	}),
}));

export const valueLegsRelations = relations(valueLegs, ({one, many}) => ({
	slipLegs: many(slipLegs),
	prediction: one(predictions, {
		fields: [valueLegs.predictionId],
		references: [predictions.id]
	}),
	oddsSnapshot: one(oddsSnapshots, {
		fields: [valueLegs.oddsSnapshotId],
		references: [oddsSnapshots.id]
	}),
	match: one(matches, {
		fields: [valueLegs.matchId],
		references: [matches.id]
	}),
}));

export const slipsRelations = relations(slips, ({many}) => ({
	slipLegs: many(slipLegs),
}));